"""Authentication and self-service account routes.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py. Endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged, so templates, url_for and the
public-endpoint allow-list keep working untouched. The app object and every
helper/global used are read off the running app module (see routes.app_module)
rather than ``from app import ...`` so this works whether app.py was started
with ``python app.py`` (module ``__main__``) or imported.
"""
from core import loginguard
from routes import app_module

_app = app_module()
app = _app.app
APP_VERSION = _app.APP_VERSION
SESSION_APP_VERSION_KEY = _app.SESSION_APP_VERSION_KEY
audit = _app.audit
check_password_hash = _app.check_password_hash
current_user = _app.current_user
datetime = _app.datetime
flash = _app.flash
generate_password_hash = _app.generate_password_hash
get_db = _app.get_db
init_db = _app.init_db
is_safe_redirect_url = _app.is_safe_redirect_url
log_activity = _app.log_activity
make_response = _app.make_response
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
session = _app.session
url_for = _app.url_for
user_is_mark_layer = _app.user_is_mark_layer


@app.route("/login", methods=["GET", "POST"])
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    """Handle username/password login for the race-officer app."""
    init_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # Rate-limit brute force per (client ip, username). Behind Cloudflare the
        # real client is in CF-Connecting-IP; remote_addr would be the tunnel.
        client_ip = request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"
        guard_key = f"{client_ip}|{username.lower()}"
        if loginguard.is_locked(guard_key):
            log_activity("login blocked", f"username={username} ip={client_ip}", user="anonymous")
            flash("Too many failed sign-in attempts. Please wait a few minutes and try again.", "error")
        else:
            with get_db() as db:
                user = db.execute("SELECT * FROM users WHERE lower(username) = lower(?) AND status = 'ACTIVE'", (username,)).fetchone()
                if user and check_password_hash(user["password_hash"], password):
                    loginguard.clear(guard_key)
                    session.clear()
                    session["user_id"] = int(user["id"])
                    session["username"] = user["username"]
                    session[SESSION_APP_VERSION_KEY] = APP_VERSION
                    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.now().isoformat(timespec="seconds"), user["id"]))
                    db.commit()
                    audit("login", f"role={user['role']}")
                    # A mark layer has one page, so send them straight to it
                    # rather than to a home page that would only bounce them
                    # back — a wasted round trip on a phone on 4G in a boat.
                    if user_is_mark_layer(user):
                        return redirect(url_for("marks_ping_page"))
                    next_url = request.form.get("next", "")
                    return redirect(next_url if is_safe_redirect_url(next_url) else url_for("index"))
            loginguard.record_failure(guard_key)
            log_activity("login failed", f"username={username}", user="anonymous")
            flash("Invalid username or password.", "error")
    next_url = request.args.get("next", "")
    # Never cache the login page. It carries a per-session CSRF token that is only
    # valid alongside the session cookie set on the same response; a cached copy
    # (browser back/forward cache or a CDN like Cloudflare) would hand a later
    # visitor a token with no matching cookie, so their sign-in POST would fail
    # the CSRF check. no-store forces a fresh page + cookie on every visit.
    response = make_response(render_template(
        "login.html", next_url=next_url if is_safe_redirect_url(next_url) else ""))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/logout")
@app.route("/admin/logout")
def logout():
    """Log the current user out."""
    audit("logout")
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@app.route("/admin/account", methods=["GET", "POST"])
def account_page():
    """Let any logged-in user change their own password.

    Deliberately not an admin-only endpoint: Settings is read-only for race
    officers, so this self-service page is the only way they can update their
    own password. It only ever touches the logged-in user's own account and
    requires the current password to confirm the change.
    """
    user = current_user()
    if not user:
        return redirect(url_for("login", next=request.path))
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not check_password_hash(user["password_hash"], current_password):
            flash("Your current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("Your new password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        else:
            with get_db() as db:
                db.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (generate_password_hash(new_password), datetime.now().isoformat(timespec="seconds"), user["id"]),
                )
                db.commit()
            audit("password changed", "own account")
            flash("Your password has been changed.", "success")
            return redirect(url_for("account_page"))
    return render_template("account.html", account_user=user)
