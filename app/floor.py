"""Dine-in domain logic: tables, reservations, tabs, kitchen flow, inventory, payment, owner tools."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from . import db
from .models import (AuditLog, Commitment, Party, PartyTable, DiningTable, Expense, Ingredient, MenuItem, Order, Payment, Payroll,
                     Recipe, Reservation, Tab, TabLine)
from .services import TAX_RATE, _fail, q2

RANK = {"Starters": 1, "Entrée": 1, "Drinks": 2, "Main": 3, "Dessert": 4}
COURSE = {1: "Starter", 2: "Drinks", 3: "Main", 4: "Dessert"}
KITCHEN = {"new": "cooking", "cooking": "ready"}
WAITER = {"ready": "picked", "picked": "served"}
ROLE_STEPS = {"kitchen": KITCHEN, "chef": KITCHEN, "waiter": WAITER, "host": WAITER,
              "admin": {**KITCHEN, **WAITER}, "owner": {**KITCHEN, **WAITER}}
HOLD = timedelta(minutes=90)


def _money(tab):
    sub = q2(sum((l.unit_price * l.qty for l in tab.lines), Decimal(0)))
    tax = q2(sub * TAX_RATE)
    return sub, tax, sub + tax


class FloorService:
    # ---- tables & reservations ----
    def tables(self):
        busy = dict(db.session.query(Tab.table_id, func.count(Tab.id)).filter(Tab.status == "open").group_by(Tab.table_id).all())
        nxt = {}
        for r in Reservation.query.filter_by(status="booked").order_by(Reservation.starts_at):
            nxt.setdefault(r.table_id, r.starts_at.strftime("%d %b %H:%M"))
        held = dict(db.session.query(PartyTable.table_id, Party.name).join(Party, Party.id == PartyTable.party_id).filter(Party.status != "open").all())
        return [dict(id=t.id, label=t.label, seats=t.seats, tabs=busy.get(t.id, 0), next=nxt.get(t.id), held=held.get(t.id))
                for t in DiningTable.query.filter_by(active=True).order_by(DiningTable.seats, DiningTable.id)]

    def add_table(self, label, seats):
        label = label.strip()[:20]
        if not label or not 1 <= seats <= 40:
            _fail("Label and 1-40 seats required")
        if DiningTable.query.filter_by(label=label).first():
            _fail("Label already used", 409)
        db.session.add(DiningTable(label=label, seats=seats))
        db.session.commit()
        return dict(ok=True)

    def edit_table(self, tid, d):
        t = db.session.get(DiningTable, tid) or _fail("Table not found", 404)
        if "seats" in d:
            if not 1 <= int(d["seats"]) <= 40:
                _fail("Seats must be 1-40")
            t.seats = int(d["seats"])
        if "label" in d and d["label"].strip():
            t.label = d["label"].strip()[:20]
        if d.get("active") is False:
            if Tab.query.filter_by(table_id=t.id, status="open").first():
                _fail("Table has open tabs", 409)
            t.active = False
        db.session.commit()
        return dict(ok=True)

    def book(self, table_id, name, party, starts_at):
        t = db.session.get(DiningTable, table_id)
        if not t or not t.active:
            _fail("Table not found", 404)
        if not name.strip():
            _fail("Name required")
        if not 1 <= party <= t.seats:
            _fail(f"Party must be 1-{t.seats} for this table")
        when = datetime.fromisoformat(starts_at)
        if Reservation.query.filter(Reservation.table_id == t.id, Reservation.status == "booked",
                                    Reservation.starts_at > when - HOLD, Reservation.starts_at < when + HOLD).first():
            _fail("Table already booked around that time", 409)
        db.session.add(Reservation(table_id=t.id, name=name.strip()[:80], party=party, starts_at=when))
        db.session.commit()
        return dict(ok=True)

    def reservations(self):
        rows = Reservation.query.filter_by(status="booked").order_by(Reservation.starts_at).limit(60)
        return [dict(id=r.id, table=r.table.label, name=r.name, party=r.party, starts=r.starts_at.strftime("%d %b %H:%M")) for r in rows]

    def cancel_reservation(self, rid):
        r = db.session.get(Reservation, rid) or _fail("Reservation not found", 404)
        r.status = "cancelled"
        db.session.commit()

    def seat(self, table_id, guest, now=None):
        t = db.session.get(DiningTable, table_id, with_for_update=True)
        if not t or not t.active:
            _fail("Table not found", 404)
        if db.session.query(PartyTable.id).join(Party, Party.id == PartyTable.party_id).filter(PartyTable.table_id == t.id, Party.status != "open").first():
            _fail("Table is held for a party reservation", 409)
        if Tab.query.filter_by(table_id=t.id, status="open").first():
            _fail("Table already occupied", 409)
        if now:
            at = datetime.fromisoformat(now)
            for r in Reservation.query.filter_by(table_id=t.id, status="booked"):
                if abs(r.starts_at - at) < HOLD:
                    r.status = "seated"
        tab = Tab(table_id=t.id, guest=(guest or "Guest 1").strip()[:60])
        db.session.add(tab)
        db.session.commit()
        return self.tab_view(tab.id)

    def add_client(self, table_id, guest):
        n = Tab.query.filter_by(table_id=table_id, status="open").count()
        if not n:
            _fail("Seat the table first", 409)
        tab = Tab(table_id=table_id, guest=(guest or f"Guest {n + 1}").strip()[:60])
        db.session.add(tab)
        db.session.commit()
        return self.tab_view(tab.id)

    # ---- tabs & lines ----
    def tabs(self):
        rows = Tab.query.filter_by(status="open").options(selectinload(Tab.lines).joinedload(TabLine.item)).order_by(Tab.table_id, Tab.id).all()
        labels = dict(db.session.query(DiningTable.id, DiningTable.label))
        return [self._tab(t, labels[t.table_id]) for t in rows]

    def tab_view(self, tab_id):
        t = db.session.get(Tab, tab_id)
        return self._tab(t, db.session.get(DiningTable, t.table_id).label)

    def _tab(self, t, label):
        sub, tax, total = _money(t)
        lines = [dict(id=l.id, name=l.item.name, qty=l.qty, rank=l.rank, status=l.status, rush=l.rush) for l in t.lines]
        return dict(id=t.id, table=label, table_id=t.table_id, guest=t.guest, lines=lines, subtotal=float(sub), tax=float(tax),
                    total=float(total), can_pay=bool(lines) and all(l.status == "served" for l in t.lines))

    def add_line(self, tab_id, item_id, qty, note=""):
        tab = db.session.get(Tab, tab_id)
        item = db.session.get(MenuItem, item_id)
        if not tab or tab.status != "open":
            _fail("Tab not open", 409)
        if not item or not item.active:
            _fail("Item unavailable", 409)
        if not 0 < qty <= 20:
            _fail("Quantity must be 1-20")
        db.session.add(TabLine(tab_id=tab.id, item_id=item.id, qty=qty, unit_price=item.final_price,
                               rank=RANK.get(item.category, 3), note=(note or "")[:120]))
        db.session.commit()
        return self.tab_view(tab.id)

    def remove_line(self, lid):
        l = db.session.get(TabLine, lid, with_for_update=True) or _fail("Line not found", 404)
        if l.status != "new":
            _fail("Already in the kitchen", 409)
        db.session.delete(l)
        db.session.commit()

    # ---- kitchen flow ----
    def advance(self, role, lid):
        l = db.session.get(TabLine, lid, with_for_update=True)
        if not l or l.tab.status != "open":
            _fail("Line not found", 404)
        nxt = ROLE_STEPS.get(role, {}).get(l.status)
        if not nxt:
            _fail("Not allowed for your role at this status", 403)
        now = datetime.utcnow()
        if l.status == "new":
            if not self._fireable(l):
                _fail("Earlier course is still in progress", 409)
            self._consume(l)
            l.cooking_at = now
        elif l.status == "cooking":
            l.ready_at = now
        elif l.status == "ready":
            l.picked_at = now
        else:
            l.served_at = now
        l.status = nxt
        db.session.commit()
        return dict(id=l.id, status=l.status)

    def rush(self, lid):
        l = db.session.get(TabLine, lid, with_for_update=True) or _fail("Line not found", 404)
        if l.status not in ("new", "cooking"):
            _fail("Too late to rush", 409)
        l.rush = not l.rush
        db.session.commit()

    @staticmethod
    def _fireable(l):
        return not db.session.query(TabLine.id).join(Tab).filter(
            Tab.table_id == l.tab.table_id, Tab.status == "open", TabLine.rank < l.rank, TabLine.status != "served").first()

    @staticmethod
    def _consume(l):
        recipes = Recipe.query.filter_by(item_id=l.item_id).order_by(Recipe.ingredient_id).all()
        need = [(db.session.get(Ingredient, r.ingredient_id, with_for_update=True), r.qty * l.qty) for r in recipes]
        for ing, n in need:
            if ing.stock < n:
                _fail(f"Not enough {ing.name} in stock", 409)
        for ing, n in need:
            ing.stock -= n

    def queue(self):
        lines = (TabLine.query.join(Tab).filter(Tab.status == "open", TabLine.status.in_(("new", "cooking", "ready")))
                 .options(joinedload(TabLine.item), joinedload(TabLine.tab)).all())
        minrank = {}
        for tid, rank in db.session.query(Tab.table_id, TabLine.rank).join(TabLine).filter(Tab.status == "open", TabLine.status != "served"):
            minrank[tid] = min(rank, minrank.get(tid, 9))
        labels = dict(db.session.query(DiningTable.id, DiningTable.label))
        now = datetime.utcnow()
        out = [dict(id=l.id, table=labels[l.tab.table_id], guest=l.tab.guest, name=l.item.name, qty=l.qty, rank=l.rank, status=l.status,
                    rush=l.rush, note=l.note, fireable=l.rank <= minrank[l.tab.table_id], wait=int((now - l.created_at).total_seconds() // 60),
                    _t=l.created_at) for l in lines]
        out.sort(key=lambda d: (not d["rush"], d["status"] == "ready", not d["fireable"], d["rank"], d["_t"]))   # rush > cooking > course order > age
        for d in out:
            del d["_t"]
        return out

    # ---- payment ----
    def pay(self, tab_id, method, tendered=None, reference=None):
        tab = db.session.get(Tab, tab_id, with_for_update=True)
        if not tab or tab.status != "open":
            _fail("Tab not open", 409)
        if not tab.lines or any(l.status != "served" for l in tab.lines):
            _fail("All items must be served before payment", 409)
        sub, tax, total = _money(tab)
        change, ref = Decimal(0), "cash"
        if method == "cash":
            paid = Decimal(str(tendered or 0))
            if paid < total:
                _fail("Cash tendered is less than the total")
            change = paid - total
        elif method == "card":   # card is taken on the external terminal; we record its approval code
            ref = (reference or "").strip()
            if len(ref) < 4:
                _fail("Card approval code from the terminal is required")
        else:
            _fail("Invalid payment method")
        db.session.add(Payment(tab_id=tab.id, method=method, reference=ref[:60], subtotal=sub, tax=tax, total=total))
        tab.status = "paid"
        db.session.add(AuditLog(kind="TAB_PAID", ref=f"tab-{tab.id}", body=f"{method} total {total} table {tab.table_id}"))
        db.session.commit()
        return dict(total=float(total), change=float(change), tab_id=tab.id)

    # ---- ingredients & recipes ----
    def ingredients(self):
        return [dict(id=i.id, name=i.name, unit=i.unit, stock=float(i.stock), low=i.stock <= (i.reorder_level or 0))
                for i in Ingredient.query.order_by(Ingredient.name)]

    def add_ingredient(self, d):
        name = d["name"].strip()[:80]
        if not name or Ingredient.query.filter_by(name=name).first():
            _fail("Name missing or already exists", 409)
        db.session.add(Ingredient(name=name, unit=d.get("unit", "pcs")[:20], stock=Decimal(str(d.get("stock") or 0)),
                                  reorder_level=Decimal(str(d.get("reorder_level") or 0))))
        db.session.commit()

    def set_recipe(self, item_id, ingredient_id, qty):
        qty = Decimal(str(qty))
        r = Recipe.query.filter_by(item_id=item_id, ingredient_id=ingredient_id).first()
        if qty <= 0:
            if r:
                db.session.delete(r)
        elif r:
            r.qty = qty
        else:
            db.session.add(Recipe(item_id=item_id, ingredient_id=ingredient_id, qty=qty))
        db.session.commit()


class OwnerService:
    def procure(self, ingredient_id, qty, cost, supplier=""):
        ing = db.session.get(Ingredient, ingredient_id, with_for_update=True) or _fail("Ingredient not found", 404)
        qty, cost = Decimal(str(qty)), Decimal(str(cost))
        if qty <= 0 or cost < 0:
            _fail("Quantity must be positive and cost non-negative")
        ing.stock += qty
        db.session.add(Expense(category="procurement", amount=cost, day=date.today(), note=f"{ing.name} x{qty} {supplier}"[:200]))
        db.session.add(AuditLog(kind="PROCUREMENT", ref=f"ing-{ing.id}", body=f"{qty} {ing.unit} for {cost} {supplier}"[:500]))
        db.session.commit()

    def add_commitment(self, name, kind, amount):
        amount = Decimal(str(amount))
        if kind not in ("rent", "lease", "subscription") or amount <= 0 or not name.strip():
            _fail("Name, kind and positive amount required")
        db.session.add(Commitment(name=name.strip()[:80], kind=kind, amount=amount))
        db.session.commit()

    def post_commitments(self, month):
        datetime.strptime(month, "%Y-%m")
        n = 0
        for c in Commitment.query.filter_by(active=True):
            note = f"{c.name} {month}"
            if not Expense.query.filter_by(category=c.kind, note=note).first():
                db.session.add(Expense(category=c.kind, amount=c.amount, day=date.today(), note=note))
                n += 1
        db.session.commit()
        return n

    def audit(self):
        return [dict(at=a.created_at.strftime("%d %b %H:%M"), kind=a.kind, ref=a.ref, body=a.body)
                for a in AuditLog.query.order_by(AuditLog.id.desc()).limit(200)]

    def analytics(self, days=14):
        since = datetime.utcnow() - timedelta(days=days)
        daily, online = {}, 0.0
        for at, tot in db.session.query(Payment.paid_at, Payment.total).filter(Payment.paid_at >= since):
            daily[at.date().isoformat()] = daily.get(at.date().isoformat(), 0.0) + float(tot)
        dine = sum(daily.values())
        for at, tot in db.session.query(Order.created_at, Order.total).filter(Order.created_at >= since, Order.status != "cancelled"):
            daily[at.date().isoformat()] = daily.get(at.date().isoformat(), 0.0) + float(tot)
            online += float(tot)
        names = dict(db.session.query(MenuItem.id, MenuItem.name))
        labels = dict(db.session.query(DiningTable.id, DiningTable.label))
        course, table, top, prep, deliver = {}, {}, {}, [], []
        for l, tid in db.session.query(TabLine, Tab.table_id).join(Tab).filter(Tab.status == "paid", TabLine.created_at >= since):
            amt = float(l.unit_price * l.qty)
            course[COURSE.get(l.rank, "Other")] = course.get(COURSE.get(l.rank, "Other"), 0) + amt
            table[labels.get(tid, "?")] = table.get(labels.get(tid, "?"), 0) + amt
            top[names[l.item_id]] = top.get(names[l.item_id], 0) + l.qty
            if l.cooking_at and l.ready_at:
                prep.append((l.ready_at - l.cooking_at).total_seconds() / 60)
            if l.ready_at and l.served_at:
                deliver.append((l.served_at - l.ready_at).total_seconds() / 60)
        avg = lambda a: round(sum(a) / len(a), 1) if a else 0
        payroll = {m: float(g) for m, g in db.session.query(Payroll.month, func.sum(Payroll.gross)).group_by(Payroll.month)}
        return dict(days=days, daily=sorted(daily.items()), summary=dict(
            dine_in_revenue=round(dine, 2), online_revenue=round(online, 2), avg_prep_minutes=avg(prep), avg_delivery_minutes=avg(deliver),
            sales_by_course={k: round(v, 2) for k, v in course.items()}, sales_by_table={k: round(v, 2) for k, v in table.items()},
            top_items=[[k, v] for k, v in sorted(top.items(), key=lambda x: -x[1])[:5]], payroll_by_month=payroll))
