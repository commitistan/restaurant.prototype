from flask import Blueprint, jsonify, request, render_template
from . import db
from .models import Commitment, DiningTable, Payment, Tab
from .routes import role_required, _svc_err
from .services import ServiceError
from .floor import FloorService, OwnerService

dine = Blueprint("dine", __name__, url_prefix="/api")
dine.register_error_handler(ServiceError, _svc_err)
for _e in (ValueError, ArithmeticError, KeyError, TypeError):
    dine.register_error_handler(_e, lambda e: (jsonify(error="Invalid input"), 400))
fl, ow = FloorService(), OwnerService()
body = lambda: request.get_json(force=True)
FRONT = ("host", "waiter", "cashier")


@dine.get("/floor/tables")
@role_required(*FRONT)
def tables(u):
    return jsonify(fl.tables())


@dine.post("/floor/tables")
@role_required("host")
def add_table(u):
    d = body()
    return jsonify(fl.add_table(d["label"], int(d["seats"]))), 201


@dine.patch("/floor/tables/<int:tid>")
@role_required("host")
def edit_table(u, tid):
    return jsonify(fl.edit_table(tid, body()))


@dine.get("/floor/reservations")
@role_required(*FRONT)
def reservations(u):
    return jsonify(fl.reservations())


@dine.post("/floor/reservations")
@role_required("host")
def book(u):
    d = body()
    return jsonify(fl.book(int(d["table_id"]), d["name"], int(d["party"]), d["starts_at"])), 201


@dine.delete("/floor/reservations/<int:rid>")
@role_required("host")
def cancel_res(u, rid):
    fl.cancel_reservation(rid)
    return "", 204


@dine.post("/floor/tables/<int:tid>/seat")
@role_required("host", "waiter")
def seat(u, tid):
    d = body()
    return jsonify(fl.seat(tid, d.get("guest", ""), d.get("now"))), 201


@dine.post("/floor/tables/<int:tid>/tabs")
@role_required("host", "waiter")
def add_client(u, tid):
    return jsonify(fl.add_client(tid, body().get("guest", ""))), 201


@dine.get("/floor/tabs")
@role_required(*FRONT)
def tabs(u):
    return jsonify(fl.tabs())


@dine.post("/floor/tabs/<int:tid>/lines")
@role_required("host", "waiter")
def add_line(u, tid):
    d = body()
    return jsonify(fl.add_line(tid, int(d["item_id"]), int(d.get("qty", 1)), d.get("note", ""))), 201


@dine.delete("/floor/lines/<int:lid>")
@role_required("host", "waiter")
def del_line(u, lid):
    fl.remove_line(lid)
    return "", 204


@dine.post("/floor/lines/<int:lid>/advance")
@role_required("host", "waiter", "kitchen", "chef")
def advance(u, lid):
    return jsonify(fl.advance(u.role, lid))


@dine.post("/floor/lines/<int:lid>/rush")
@role_required("chef")
def rush(u, lid):
    fl.rush(lid)
    return "", 204


@dine.get("/floor/kitchen")
@role_required("kitchen", "chef")
def kitchen(u):
    return jsonify(fl.queue())


@dine.post("/floor/tabs/<int:tid>/pay")
@role_required("cashier", "waiter")
def pay(u, tid):
    d = body()
    return jsonify(fl.pay(tid, d.get("method"), d.get("tendered"), d.get("reference")))


@dine.get("/floor/tabs/<int:tid>/receipt")
@role_required("cashier", "waiter")
def receipt(u, tid):
    tab, p = db.session.get(Tab, tid), Payment.query.filter_by(tab_id=tid).first()
    if not tab or not p:
        return jsonify(error="Not found"), 404
    return render_template("tab_receipt.html", tab=tab, p=p, table=db.session.get(DiningTable, tab.table_id))


@dine.get("/floor/ingredients")
@role_required("chef", "kitchen")
def ingredients(u):
    return jsonify(fl.ingredients())


@dine.post("/floor/ingredients")
@role_required("chef")
def add_ing(u):
    fl.add_ingredient(body())
    return "", 201


@dine.post("/floor/recipes")
@role_required("chef")
def recipe(u):
    d = body()
    fl.set_recipe(int(d["item_id"]), int(d["ingredient_id"]), d["qty"])
    return "", 204


@dine.post("/owner/procurement")
@role_required("owner")
def procure(u):
    d = body()
    ow.procure(int(d["ingredient_id"]), d["qty"], d["cost"], d.get("supplier", ""))
    return "", 201


@dine.route("/owner/commitments", methods=["GET", "POST"])
@role_required("owner")
def commitments(u):
    if request.method == "POST":
        d = body()
        ow.add_commitment(d["name"], d["kind"], d["amount"])
        return "", 201
    return jsonify([dict(name=c.name, kind=c.kind, amount=float(c.amount)) for c in Commitment.query.filter_by(active=True)])


@dine.post("/owner/commitments/post")
@role_required("owner")
def post_commitments(u):
    return jsonify(posted=ow.post_commitments(body()["month"]))


@dine.get("/owner/audit")
@role_required("owner")
def audit(u):
    return jsonify(ow.audit())


@dine.get("/owner/analytics")
@role_required("owner")
def analytics(u):
    return jsonify(ow.analytics())
