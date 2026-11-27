from datetime import datetime
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from . import db



class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    _pw = db.Column("password_hash", db.String(255), nullable=False)
    role = db.Column(db.String(20), default="customer", nullable=False)
    points = db.Column(db.Integer, default=0, nullable=False)

    def set_password(self, raw):
        self._pw = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self._pw, raw)


class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.String(200), default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)
    discount_pct = db.Column(db.Integer, default=0)
    stock = db.Column(db.Integer, default=0, nullable=False)
    active = db.Column(db.Boolean, default=True)

    @property
    def final_price(self):
        return (self.price * (100 - self.discount_pct) / 100).quantize(Decimal("0.01"))

    def to_dict(self):
        return dict(id=self.id, name=self.name, category=self.category, description=self.description,
                    price=float(self.price), final_price=float(self.final_price),
                    discount_pct=self.discount_pct, available=self.stock > 0)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    status = db.Column(db.String(20), default="placed", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    subtotal = db.Column(db.Numeric(10, 2), default=0)
    tax = db.Column(db.Numeric(10, 2), default=0)
    points_used = db.Column(db.Integer, default=0)
    points_earned = db.Column(db.Integer, default=0)
    total = db.Column(db.Numeric(10, 2), default=0)
    payment_method = db.Column(db.String(10))
    payment_ref = db.Column(db.String(60))
    items = db.relationship("OrderItem", cascade="all, delete-orphan", lazy="joined")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), index=True, nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("menu_item.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    item = db.relationship("MenuItem", lazy="joined")


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, default=datetime.utcnow, index=True)
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    note = db.Column(db.String(200), default="")


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    position = db.Column(db.String(60), default="")
    monthly_salary = db.Column(db.Numeric(10, 2), nullable=False)
    tax_rate = db.Column(db.Numeric(4, 3), default=Decimal("0.10"))
    active = db.Column(db.Boolean, default=True)


class Payroll(db.Model):
    __table_args__ = (db.UniqueConstraint("employee_id", "month"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False, index=True)
    gross = db.Column(db.Numeric(10, 2), nullable=False)
    tax = db.Column(db.Numeric(10, 2), nullable=False)
    net = db.Column(db.Numeric(10, 2), nullable=False)
    paid_on = db.Column(db.Date, default=datetime.utcnow)


ROLES = ("customer", "host", "waiter", "kitchen", "chef", "cashier", "owner", "admin")


class Ingredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    unit = db.Column(db.String(20), default="pcs")
    stock = db.Column(db.Numeric(12, 3), default=0, nullable=False)
    reorder_level = db.Column(db.Numeric(12, 3), default=0)


class Recipe(db.Model):
    __table_args__ = (db.UniqueConstraint("item_id", "ingredient_id"),)
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("menu_item.id"), nullable=False, index=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredient.id"), nullable=False)
    qty = db.Column(db.Numeric(12, 3), nullable=False)


class DiningTable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(20), unique=True, nullable=False)
    seats = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=True)


class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, db.ForeignKey("dining_table.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    party = db.Column(db.Integer, nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(10), default="booked", index=True)
    table = db.relationship("DiningTable")


class Tab(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, db.ForeignKey("dining_table.id"), nullable=False, index=True)
    guest = db.Column(db.String(60), default="Guest")
    status = db.Column(db.String(10), default="open", index=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    lines = db.relationship("TabLine", backref="tab", cascade="all, delete-orphan", order_by="TabLine.id")


class TabLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tab_id = db.Column(db.Integer, db.ForeignKey("tab.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("menu_item.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    rank = db.Column(db.Integer, default=3)          # 1 starter, 2 drinks, 3 main, 4 dessert
    status = db.Column(db.String(10), default="new", index=True)   # new > cooking > ready > picked > served
    rush = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    cooking_at = db.Column(db.DateTime)
    ready_at = db.Column(db.DateTime)
    picked_at = db.Column(db.DateTime)
    served_at = db.Column(db.DateTime)
    item = db.relationship("MenuItem")


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tab_id = db.Column(db.Integer, db.ForeignKey("tab.id"), unique=True, nullable=False)
    method = db.Column(db.String(10), nullable=False)
    reference = db.Column(db.String(60), default="")
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    tax = db.Column(db.Numeric(10, 2), nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Commitment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), nullable=False)   # rent | lease | subscription
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    active = db.Column(db.Boolean, default=True)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), index=True)
    ref = db.Column(db.String(40))
    body = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Party(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), default="reserved", index=True)   # reserved > active > open (open to all)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PartyTable(db.Model):
    __table_args__ = (db.UniqueConstraint("party_id", "table_id"),)
    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False, index=True)
    table_id = db.Column(db.Integer, db.ForeignKey("dining_table.id"), nullable=False, index=True)


class Guest(db.Model):
    __table_args__ = (db.UniqueConstraint("party_id", "guest_no"),)
    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False, index=True)
    guest_no = db.Column(db.Integer, nullable=False)
    household = db.Column(db.String(40), default="")
    tier = db.Column(db.String(40), default="")
    side_group = db.Column(db.String(60), default="")
    party_role = db.Column(db.String(60), default="")
    title = db.Column(db.String(20), default="")
    first_name = db.Column(db.String(60), default="")
    last_name = db.Column(db.String(60), default="")
    place_card = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(10), default="Adult")
    entree = db.Column(db.String(60), default="")
    dietary = db.Column(db.String(120), default="")
    accessibility = db.Column(db.String(120), default="")
    table_id = db.Column(db.Integer, db.ForeignKey("dining_table.id"))
    seat_no = db.Column(db.Integer)
    pass_hash = db.Column(db.String(64))          # HMAC of the one-time pass code; plaintext is never stored
    failed_attempts = db.Column(db.Integer, default=0)
    status = db.Column(db.String(12), default="invited", index=True)   # invited > checked_in > checked_out | no_show
    checked_in_at = db.Column(db.DateTime)
    checked_out_at = db.Column(db.DateTime)
    checked_in_by = db.Column(db.Integer)
    tab_id = db.Column(db.Integer, db.ForeignKey("tab.id"))
