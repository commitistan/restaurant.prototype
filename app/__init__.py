import os
import click
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache

db, cache = SQLAlchemy(), Cache()


def create_app():
    app = Flask(__name__)
    uri = os.getenv("DATABASE_URL", "sqlite:///dev.db")
    for prefix in ("postgres://", "postgresql://"):   # pin the psycopg2 driver explicitly
        if uri.startswith(prefix):
            uri = "postgresql+psycopg2://" + uri[len(prefix):]
    redis = os.getenv("REDIS_URL")
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 10, "pool_pre_ping": True} if uri.startswith("postgresql") else {},
        CACHE_TYPE="RedisCache" if redis else "SimpleCache",
        CACHE_REDIS_URL=redis, CACHE_DEFAULT_TIMEOUT=60,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.getenv("PROD")), MAX_CONTENT_LENGTH=1_000_000,
    )
    db.init_app(app)
    cache.init_app(app)
    from .routes import api, auth, pages
    from .backoffice import staff, staff_pages
    from .dinein import dine
    from .party import party_bp
    svc = os.getenv("SERVICE", "all")   # all | ordering | backoffice -> one image, separately deployable services
    for bp, on in ((pages, 1), (auth, 1), (api, svc in ("all", "ordering")),
                   (staff, svc in ("all", "backoffice")), (staff_pages, svc in ("all", "backoffice")), (dine, svc in ("all", "backoffice")), (party_bp, svc in ("all", "backoffice"))):
        if on:
            app.register_blueprint(bp)

    @app.cli.command("seed")
    def seed():
        """Create tables, the admin user and the menu from menu.csv."""
        from .models import User, MenuItem, DiningTable
        from .services import MenuService
        db.create_all()
        email = os.environ["ADMIN_EMAIL"]
        if not User.query.filter_by(email=email).first():
            u = User(name="Admin", email=email, role="admin")
            u.set_password(os.environ["ADMIN_PASSWORD"])
            db.session.add(u)
        if not MenuItem.query.first():   # first boot only: never overwrite live prices/stock on restart
            with open(os.path.join(os.path.dirname(__file__), "..", "menu.csv"), encoding="utf-8") as f:
                click.echo(f"menu rows: {MenuService().import_csv(f.read())}")
        if not DiningTable.query.first():   # 10 tables for 2 guests, 3 tables for 15 guests; edit in the Host tab
            db.session.add_all([DiningTable(label=f"T{n}", seats=2) for n in range(1, 11)] + [DiningTable(label=f"L{n}", seats=15) for n in range(1, 4)])
        db.session.commit()

    return app
