"""Party reservations: guest lists, seating, strict identity check-in/out, and guest analytics."""
import csv, hashlib, hmac, io, secrets
from datetime import date, datetime
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func
from . import db
from .models import AuditLog, DiningTable, Guest, Party, PartyTable, Payment, Tab, TabLine
from .routes import role_required, _svc_err
from .services import ServiceError, _fail

MAX_FAILS = 3
TIER = lambda t: 0 if t.lower().startswith("vip") else 1 if t.lower().startswith("a") else 2 if t.lower().startswith("b") else 3


def _hash(code):
    return hmac.new(current_app.config["SECRET_KEY"].encode(), (code or "").strip().upper().encode(), hashlib.sha256).hexdigest()


def _log(kind, ref, body):
    db.session.add(AuditLog(kind=kind, ref=ref[:40], body=body[:500]))


class PartyService:
    # ---- parties ----
    def _party(self, pid):
        return db.session.get(Party, pid) or _fail("Party not found", 404)

    def _tables(self, pid):
        return DiningTable.query.join(PartyTable, PartyTable.table_id == DiningTable.id).filter(PartyTable.party_id == pid).order_by(DiningTable.seats.desc(), DiningTable.id).all()

    def _editable(self, pid):
        p = self._party(pid)
        if p.status == "open":
            _fail("Party is closed (open to all)", 409)
        return p

    def create(self, name, event_date, table_ids):
        name, day = name.strip()[:80], date.fromisoformat(event_date)
        ids = sorted({int(i) for i in table_ids})
        if not name or not ids:
            _fail("Party name and at least one table are required")
        if DiningTable.query.filter(DiningTable.id.in_(ids), DiningTable.active.is_(True)).count() != len(ids):
            _fail("Unknown table")
        held = (db.session.query(DiningTable.label).join(PartyTable, PartyTable.table_id == DiningTable.id)
                .join(Party, Party.id == PartyTable.party_id).filter(Party.status != "open", DiningTable.id.in_(ids)).first())
        if held:
            _fail(f"Table {held[0]} is already held by another party", 409)
        if Tab.query.filter(Tab.table_id.in_(ids), Tab.status == "open").first():
            _fail("A selected table has open tabs", 409)
        p = Party(name=name, event_date=day)
        db.session.add(p)
        db.session.flush()
        db.session.add_all([PartyTable(party_id=p.id, table_id=i) for i in ids])
        db.session.commit()
        return dict(id=p.id)

    def parties(self):
        out = []
        for p in Party.query.order_by(Party.event_date.desc(), Party.id.desc()).limit(30):
            counts = dict(db.session.query(Guest.status, func.count(Guest.id)).filter_by(party_id=p.id).group_by(Guest.status).all())
            tb = self._tables(p.id)
            out.append(dict(id=p.id, name=p.name, date=str(p.event_date), status=p.status, seats=sum(t.seats for t in tb),
                            invited=sum(counts.values()), checked_in=counts.get("checked_in", 0) + counts.get("checked_out", 0)))
        return out

    def detail(self, pid):
        p, tbls = self._party(pid), self._tables(pid)
        label = {t.id: t.label for t in tbls}
        guests = Guest.query.filter_by(party_id=pid).order_by(Guest.guest_no).all()
        return dict(id=p.id, name=p.name, status=p.status, tables=[dict(id=t.id, label=t.label, seats=t.seats) for t in tbls],
                    guests=[dict(id=g.id, no=g.guest_no, place=g.place_card, tier=g.tier, group=g.side_group, role=g.party_role,
                                 category=g.category, entree=g.entree, dietary=g.dietary, accessibility=g.accessibility, table_id=g.table_id,
                                 table=label.get(g.table_id, ""), seat=g.seat_no, status=g.status, has_pass=bool(g.pass_hash),
                                 locked=g.failed_attempts >= MAX_FAILS) for g in guests])

    # ---- guest list ----
    def import_csv(self, pid, text):
        """Imports only what the restaurant needs. Addresses, travel, gifts and contact columns are deliberately ignored."""
        self._editable(pid)
        tmap = {t.label: t for t in self._tables(pid)}
        taken = {(g.table_id, g.seat_no) for g in Guest.query.filter_by(party_id=pid)}
        nxt = (db.session.query(func.coalesce(func.max(Guest.guest_no), 0)).filter_by(party_id=pid).scalar() or 0) + 1
        done = skipped = 0
        for raw in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            first, last = r.get("first name", ""), r.get("last name", "")
            place = r.get("place card name") or f"{first} {last}".strip()
            if not place or r.get("ceremony & reception", "").lower() == "declined":
                skipped += 1
                continue
            no = int(r["guest id"]) if r.get("guest id", "").isdigit() else nxt
            nxt = max(nxt, no + 1)
            g = Guest.query.filter_by(party_id=pid, guest_no=no).first() or Guest(party_id=pid, guest_no=no)
            if g.status in ("checked_in", "checked_out"):
                skipped += 1
                continue
            g.household, g.tier, g.side_group = r.get("household id", "")[:40], r.get("vip / tier", "")[:40], r.get("side & group", "")[:60]
            g.party_role, g.title, g.first_name, g.last_name = r.get("party / bridal role", "")[:60], r.get("formal title / prefix", "")[:20], first[:60], last[:60]
            g.place_card, g.category = place[:120], (r.get("adult / child / vendor") or "Adult")[:10]
            g.entree = (r.get("entrée selection") or r.get("entree selection", ""))[:60]
            g.dietary, g.accessibility = r.get("dietary & allergies", "")[:120], r.get("accessibility / mobility", "")[:120]
            tv = r.get("table number", "")
            t = tmap.get(tv) or tmap.get("T" + tv)
            sv = r.get("seat number", "")
            if t and sv.isdigit() and 1 <= int(sv) <= t.seats and (t.id, int(sv)) not in taken:
                g.table_id, g.seat_no = t.id, int(sv)
                taken.add((t.id, int(sv)))
            db.session.add(g)
            done += 1
        db.session.commit()
        return dict(imported=done, skipped=skipped)

    def add_guest(self, pid, d):
        self._editable(pid)
        no = (db.session.query(func.coalesce(func.max(Guest.guest_no), 0)).filter_by(party_id=pid).scalar() or 0) + 1
        first, last = d.get("first", "").strip(), d.get("last", "").strip()
        if not (first or last):
            _fail("A name is required")
        db.session.add(Guest(party_id=pid, guest_no=no, first_name=first[:60], last_name=last[:60], place_card=f"{first} {last}".strip()[:120],
                             tier=d.get("tier", "")[:40], household=d.get("household", "")[:40], dietary=d.get("dietary", "")[:120]))
        db.session.commit()

    def arrange(self, pid):
        self._editable(pid)
        tbls = self._tables(pid)
        kept = Guest.query.filter(Guest.party_id == pid, Guest.status.in_(("checked_in", "checked_out"))).all()
        todo = Guest.query.filter_by(party_id=pid, status="invited").all()
        free = {t.id: [s for s in range(1, t.seats + 1) if s not in {g.seat_no for g in kept if g.table_id == t.id}] for t in tbls}
        room = sum(len(v) for v in free.values())
        if len(todo) > room:
            _fail(f"Not enough seats: {len(todo)} guests, {room} free seats")
        groups = {}
        for g in todo:
            groups.setdefault(g.household or f"solo-{g.id}", []).append(g)
        for grp in sorted(groups.values(), key=lambda gs: (min(TIER(g.tier) for g in gs), -len(gs))):   # VIPs first, households kept together
            spot = next((t for t in tbls if len(free[t.id]) >= len(grp)), None)
            for g in grp:
                t = spot or next(t for t in tbls if free[t.id])
                g.table_id, g.seat_no = t.id, free[t.id].pop(0)
        db.session.commit()

    def seat_guest(self, gid, table_id, seat_no):
        g = db.session.get(Guest, gid, with_for_update=True) or _fail("Guest not found", 404)
        self._editable(g.party_id)
        t = next((t for t in self._tables(g.party_id) if t.id == table_id), None) or _fail("Table is not part of this party")
        if g.status != "invited":
            _fail("Guest has already arrived or left", 409)
        if not 1 <= seat_no <= t.seats:
            _fail(f"Seat must be 1-{t.seats}")
        if Guest.query.filter(Guest.table_id == t.id, Guest.seat_no == seat_no, Guest.id != g.id, Guest.status != "no_show").first():
            _fail("Seat already taken", 409)
        g.table_id, g.seat_no = t.id, seat_no
        db.session.commit()

    # ---- strict identity: pass code + photo ID, 3 strikes, everything audited ----
    def issue_passes(self, pid):
        self._editable(pid)
        out = []
        for g in Guest.query.filter_by(party_id=pid, status="invited", pass_hash=None).order_by(Guest.guest_no):
            code = secrets.token_hex(3).upper()
            g.pass_hash, g.failed_attempts = _hash(code), 0
            out.append(dict(no=g.guest_no, place=g.place_card, code=code))
        _log("PASSES_ISSUED", f"party-{pid}", f"{len(out)} passes")
        db.session.commit()
        return out

    def unlock(self, gid):
        g = db.session.get(Guest, gid, with_for_update=True) or _fail("Guest not found", 404)
        g.pass_hash, g.failed_attempts = None, 0
        _log("PASS_RESET", f"guest-{g.id}", "pass cleared; issue a new one")
        db.session.commit()

    def check_in(self, gid, code, id_checked, user):
        g = db.session.get(Guest, gid, with_for_update=True) or _fail("Guest not found", 404)
        p = self._editable(g.party_id)
        if g.status != "invited":
            _fail(f"Guest is already {g.status.replace('_', ' ')}", 409)
        if not id_checked:
            _fail("Photo ID must be checked against the list name first")
        if not g.pass_hash:
            _fail("No pass issued for this guest. Ask an admin to issue passes", 409)
        if g.failed_attempts >= MAX_FAILS:
            _fail("Guest locked after failed attempts. An admin must reset the pass", 403)
        if not hmac.compare_digest(g.pass_hash, _hash(code)):
            g.failed_attempts += 1
            _log("CHECKIN_FAIL", f"guest-{g.id}", f"party {p.id} #{g.guest_no} attempt {g.failed_attempts} by user {user.id}")
            db.session.commit()
            _fail("Pass code does not match", 403)
        if not g.table_id or not g.seat_no:
            _fail("Assign a table and seat first", 409)
        tab = Tab(table_id=g.table_id, guest=(f"Privileged #{g.guest_no}" + (f" - {g.dietary}" if g.dietary else ""))[:60])
        db.session.add(tab)
        db.session.flush()
        g.tab_id, g.status, g.id_verified = tab.id, "checked_in", True
        g.checked_in_at, g.checked_in_by, g.failed_attempts = datetime.utcnow(), user.id, 0
        p.status = "active"
        _log("GUEST_CHECKIN", f"guest-{g.id}", f"party {p.id} #{g.guest_no} by user {user.id}")
        db.session.commit()

    def check_out(self, gid, user):
        g = db.session.get(Guest, gid, with_for_update=True) or _fail("Guest not found", 404)
        if g.status != "checked_in":
            _fail("Guest is not checked in", 409)
        tab = db.session.get(Tab, g.tab_id)
        if tab and tab.status == "open":
            if tab.lines:
                _fail("Serve and settle this guest's tab on the Floor tab first", 409)
            g.tab_id = None
            db.session.flush()
            db.session.delete(tab)   # nothing was ordered
        g.status, g.checked_out_at = "checked_out", datetime.utcnow()
        _log("GUEST_CHECKOUT", f"guest-{g.id}", f"party {g.party_id} #{g.guest_no} by user {user.id}")
        self._maybe_open(db.session.get(Party, g.party_id))
        db.session.commit()

    def no_show(self, gid, user):
        g = db.session.get(Guest, gid, with_for_update=True) or _fail("Guest not found", 404)
        if g.status != "invited":
            _fail("Only expected guests can be marked no-show", 409)
        g.status = "no_show"
        _log("GUEST_NOSHOW", f"guest-{g.id}", f"party {g.party_id} #{g.guest_no} by user {user.id}")
        self._maybe_open(db.session.get(Party, g.party_id))
        db.session.commit()

    @staticmethod
    def _maybe_open(p):
        db.session.flush()
        if p.status != "open" and not Guest.query.filter(Guest.party_id == p.id, Guest.status.in_(("invited", "checked_in"))).first():
            p.status = "open"   # every guest served and gone: tables are released, open to all
            _log("PARTY_OPEN", f"party-{p.id}", "all guests finished")

    # ---- analytics ----
    def analytics(self):
        rows = db.session.query(Guest, Payment.total).outerjoin(Payment, Payment.tab_id == Guest.tab_id).all()
        qty = dict(db.session.query(TabLine.tab_id, func.sum(TabLine.qty)).group_by(TabLine.tab_id).all())
        by_tier, by_group, stays, spends, items, people = {}, {}, [], [], [], []
        status = {}
        for g, tot in rows:
            status[g.status] = status.get(g.status, 0) + 1
            if g.status not in ("checked_in", "checked_out"):
                continue
            spend = float(tot or 0)
            spends.append(spend)
            items.append(int(qty.get(g.tab_id, 0)))
            by_tier[g.tier or "none"] = by_tier.get(g.tier or "none", 0) + spend
            by_group[g.side_group or "none"] = by_group.get(g.side_group or "none", 0) + spend
            stay = (g.checked_out_at - g.checked_in_at).total_seconds() / 60 if g.checked_out_at and g.checked_in_at else None
            if stay is not None:
                stays.append(stay)
            people.append((f"#{g.guest_no} {g.place_card}", spend, round(stay) if stay is not None else None))
        avg = lambda a: round(sum(a) / len(a), 1) if a else 0
        expected = sum(status.values())
        return dict(guests_on_lists=expected, by_status=status,
                    attendance_rate_pct=round(100 * len(spends) / expected, 1) if expected else 0,
                    avg_stay_minutes=avg(stays), avg_spend_per_guest=avg(spends), avg_items_per_guest=avg(items),
                    spend_by_tier={k: round(v, 2) for k, v in by_tier.items()}, spend_by_group={k: round(v, 2) for k, v in by_group.items()},
                    top_spenders=[[n, s] for n, s, _ in sorted(people, key=lambda x: -x[1])[:5]],
                    longest_stays_minutes=[[n, m] for n, _, m in sorted((p for p in people if p[2] is not None), key=lambda x: -x[2])[:5]])


party_bp = Blueprint("party", __name__, url_prefix="/api/party")
party_bp.register_error_handler(ServiceError, _svc_err)
for _e in (ValueError, ArithmeticError, KeyError, TypeError):
    party_bp.register_error_handler(_e, lambda e: (jsonify(error="Invalid input"), 400))
svc = PartyService()
body = lambda: request.get_json(force=True)


@party_bp.get("/parties")
@role_required("host")
def parties(u):
    return jsonify(svc.parties())


@party_bp.post("/parties")
@role_required("host")
def create(u):
    d = body()
    return jsonify(svc.create(d["name"], d["date"], d["table_ids"])), 201


@party_bp.get("/parties/<int:pid>")
@role_required("host")
def detail(u, pid):
    return jsonify(svc.detail(pid))


@party_bp.post("/parties/<int:pid>/import")
@role_required("host")
def import_csv(u, pid):
    return jsonify(svc.import_csv(pid, request.get_data(as_text=True)))


@party_bp.post("/parties/<int:pid>/guests")
@role_required("host")
def add_guest(u, pid):
    svc.add_guest(pid, body())
    return "", 201


@party_bp.post("/parties/<int:pid>/arrange")
@role_required("host")
def arrange(u, pid):
    svc.arrange(pid)
    return "", 204


@party_bp.post("/parties/<int:pid>/passes")
@role_required("admin")
def passes(u, pid):
    return jsonify(svc.issue_passes(pid))


@party_bp.patch("/guests/<int:gid>/seat")
@role_required("host")
def seat(u, gid):
    d = body()
    svc.seat_guest(gid, int(d["table_id"]), int(d["seat_no"]))
    return "", 204


@party_bp.post("/guests/<int:gid>/checkin")
@role_required("host")
def checkin(u, gid):
    d = body()
    svc.check_in(gid, d.get("code"), bool(d.get("id_checked")), u)
    return "", 204


@party_bp.post("/guests/<int:gid>/checkout")
@role_required("host")
def checkout(u, gid):
    svc.check_out(gid, u)
    return "", 204


@party_bp.post("/guests/<int:gid>/noshow")
@role_required("host")
def noshow(u, gid):
    svc.no_show(gid, u)
    return "", 204


@party_bp.post("/guests/<int:gid>/unlock")
@role_required("admin")
def unlock(u, gid):
    svc.unlock(gid)
    return "", 204


@party_bp.get("/analytics")
@role_required("admin")
def analytics(u):
    return jsonify(svc.analytics())
