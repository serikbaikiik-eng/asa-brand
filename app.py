import os, re, csv, io, json, time, hmac, secrets, socket, sqlite3, shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, g, render_template, request, jsonify, session,
                   redirect, url_for, abort, Response, flash)

try:
    from PIL import Image
except ImportError:      # без Pillow фото сохраняются как есть
    Image = None
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE, "shop.db"))
SEED_FILE = os.path.join(BASE, "seed", "catalog.json")   # каталог для автозагрузки (см. export_catalog.py)
WHATSAPP = os.environ.get("WHATSAPP", "77051511150")
SETUP_KEY = os.environ.get("SETUP_KEY", "")          # для первой настройки на хостинге
ENV_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # необязательно: создать админа автоматически
ENV_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
STATUSES = [("new", "Новый"), ("confirmed", "Подтверждён"), ("paid", "Оплачен"),
            ("shipped", "Отправлен"), ("done", "Выполнен"), ("cancelled", "Отменён")]
SOLD = ("paid", "shipped", "done")          # считаются продажами
OPEN = ("new", "confirmed")                  # в работе
PAYS = {"kaspi": "Kaspi", "cash": "Наличными", "transfer": "Переводом"}
DEFAULTS = {"store_name": "ASA BRAND", "emblem": "♛", "accent": "#d4af6a", "whatsapp": WHATSAPP,
            "instagram": "_asa_kz", "kaspi_link": "", "currency": "₸"}
TEXT_KEYS = [("kicker", "Надпись над заголовком"), ("h1", "Главный заголовок"),
             ("lead", "Описание под заголовком"), ("howp", "Текст блока «Как заказать»")]
LANG_NAMES = [("kk", "Қазақша"), ("ru", "Русский"), ("en", "English")]
DEFAULT_TEXTS = {
    "ru": {"kicker": "В каждом аромате — свой стиль", "h1": "Премиум-парфюмерия",
           "lead": "Брендовые ароматы на разлив и боксы на 3 или 5 флаконов. Доставка по всему Казахстану.",
           "howp": "Добавьте ароматы в корзину, оформите заказ и выберите оплату. Для подтверждения мы свяжемся с вами в WhatsApp."},
    "kk": {"kicker": "Әр иісте өз стилің!", "h1": "Премиум хош иістер",
           "lead": "Брендтік хош иістер құйып сатылады, сондай-ақ 3 немесе 5 флаконнан тұратын бокстар бар. Бүкіл Қазақстан бойынша жеткізу.",
           "howp": "Хош иістерді себетке салып, тапсырысты рәсімдеңіз де, төлем түрін таңдаңыз. Растау үшін WhatsApp арқылы хабарласамыз."},
    "en": {"kicker": "A style of your own in every scent", "h1": "Premium perfume",
           "lead": "Designer fragrances by the millilitre and boxes of 3 or 5 vials. Delivery across Kazakhstan.",
           "howp": "Add fragrances to your cart, place the order and choose a payment method. We will confirm it with you on WhatsApp."}}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
GENDERS = {"men": "Мужские", "women": "Женские", "unisex": "Унисекс"}
MAX_EXTRA_PHOTOS = 10      # дополнительных фото на один товар (кроме главного)
YT_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/))([A-Za-z0-9_-]{11})")

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
                  MAX_CONTENT_LENGTH=64 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

SEED = [  # id, бренд, название, пол, ноты ru, ноты kk, ноты en
    (1, "Tom Ford", "Ombré Leather Parfum 2021", "men", "Кожа, жасмин, ирис, кедр", "Былғары, жасмин, ирис тамыры, кедр", "Leather, jasmine, orris, cedar"),
    (2, "Initio", "Oud for Greatness", "unisex", "Шафран, уд, лаванда, мускус", "Шафран, уд, лаванда, мускус", "Saffron, oud, lavender, musk"),
    (3, "Ex Nihilo", "Blue Talisman", "unisex", "Груша, бергамот, имбирь, кедр", "Алмұрт, бергамот, зімбір, кедр", "Pear, bergamot, ginger, cedar"),
    (4, "Tom Ford", "Tobacco Vanille", "unisex", "Табак, ваниль, какао, тонка", "Темекі, ваниль, какао, тонка бұршағы", "Tobacco, vanilla, cocoa, tonka"),
    (5, "Burberry", "Goddess", "women", "Ваниль, лаванда, какао, имбирь", "Ваниль, лаванда, какао, зімбір", "Vanilla, lavender, cocoa, ginger"),
    (6, "Kilian", "Good Girl Gone Bad", "women", "Османтус, персик, тубероза, амбра", "Османтус, шабдалы, тубероза, амбра", "Osmanthus, peach, tuberose, amber"),
    (7, "D&G", "L’Imperatrice 3", "women", "Маракуйя, ревень, пион, сандал", "Маракуйя, ревень, пион, сандал", "Passion fruit, rhubarb, peony, sandalwood"),
    (8, "Lancôme", "La Vie Est Belle", "women", "Чёрная смородина, ирис, пралине, ваниль", "Қара қарақат, ирис, пралине, ваниль", "Black currant, iris, praline, vanilla"),
    (9, "Chanel", "Bleu de Chanel Eau de Parfum", "men", "Грейпфрут, имбирь, кедр, сандал", "Грейпфрут, зімбір, кедр, сандал", "Grapefruit, ginger, cedar, sandalwood"),
    (10, "Emporio Armani", "Stronger With You Absolutely", "men", "Ром, лаванда, ваниль, каштан", "Ром, лаванда, ваниль, каштан", "Rum, lavender, vanilla, chestnut"),
    (11, "Dior", "Sauvage Elixir", "men", "Кардамон, лаванда, амбра, ветивер", "Кардамон, лаванда, амбра, ветивер", "Cardamom, lavender, amber, vetiver"),
    (12, "Jean Paul Gaultier", "Le Male Elixir", "men", "Лаванда, мята, ваниль, тонка", "Лаванда, жалбыз, ваниль, тонка бұршағы", "Lavender, mint, vanilla, tonka"),
]

def now_kz():
    return (datetime.now(timezone.utc) + timedelta(hours=5)).replace(tzinfo=None)

def import_seed(con):
    """Пустая база (например, после перезапуска на бесплатном хостинге): берём каталог из seed/catalog.json."""
    if con.execute("SELECT 1 FROM products LIMIT 1").fetchone() or not os.path.exists(SEED_FILE):
        return
    try:
        with open(SEED_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    pc = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    for p in data.get("products", []):
        row = {k: v for k, v in p.items() if k in pc}
        con.execute("INSERT INTO products(%s) VALUES(%s)" % (",".join(row), ",".join("?" * len(row))), list(row.values()))
    for m in data.get("media", []):
        con.execute("INSERT INTO product_media(product_id,kind,file) VALUES(?,?,?)", (m["product_id"], m["kind"], m["file"]))
    for k, v in data.get("settings", {}).items():
        con.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, v))
    src, dst = os.path.join(BASE, "seed", "img"), os.path.join(BASE, "static", "img")
    os.makedirs(dst, exist_ok=True)
    if os.path.isdir(src):
        for fn in os.listdir(src):
            if not os.path.exists(os.path.join(dst, fn)):
                shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, brand TEXT, name TEXT,
        gender TEXT, price INTEGER, img TEXT, note TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, name TEXT, phone TEXT,
        city TEXT, address TEXT, delivery TEXT, pay TEXT, note TEXT, total INTEGER, status TEXT);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, kind TEXT,
        product_id INTEGER, qty INTEGER, amount INTEGER, set_n INTEGER, picks TEXT);
    """)
    con.execute("CREATE TABLE IF NOT EXISTS product_media(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, "
                "kind TEXT, file TEXT)")   # kind: img — доп. фото, video — файл, yt — ссылка YouTube
    con.execute("CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY CHECK(id=1), login TEXT, pw TEXT)")
    if ENV_PASSWORD and len(ENV_PASSWORD) >= 8 and not con.execute("SELECT 1 FROM admins").fetchone():
        con.execute("INSERT INTO admins VALUES(1,?,?)", (ENV_LOGIN, generate_password_hash(ENV_PASSWORD)))
    if not con.execute("SELECT 1 FROM products LIMIT 1").fetchone() and not os.environ.get("NO_DEMO") and not os.path.exists(SEED_FILE):
        for pid, b, n, gd, note, _, _ in SEED:
            con.execute("INSERT INTO products(id,brand,name,gender,price,img,note,active) VALUES(?,?,?,?,?,?,?,1)",
                        (pid, b, n, gd, 600, f"{pid}.jpg", note))
    def cols(tb):
        return {r[1] for r in con.execute(f"PRAGMA table_info({tb})")}
    if "note_kk" not in cols("products"):
        con.execute("ALTER TABLE products ADD COLUMN note_kk TEXT")
        con.execute("ALTER TABLE products ADD COLUMN note_en TEXT")
    if "lang" not in cols("orders"):
        con.execute("ALTER TABLE orders ADD COLUMN lang TEXT")
    for pid, b, n, gd, _, kk, en in SEED:
        con.execute("UPDATE products SET note_kk=?,note_en=? WHERE id=? AND name=? AND note_kk IS NULL", (kk, en, pid, n))
    if "desc_ru" not in cols("products"):
        for c in ("desc_ru", "desc_kk", "desc_en"):
            con.execute(f"ALTER TABLE products ADD COLUMN {c} TEXT")
    if "sort" not in cols("products"):
        con.execute("ALTER TABLE products ADD COLUMN sort INTEGER")
    con.execute("UPDATE products SET sort=id WHERE sort IS NULL")
    import_seed(con)
    if not con.execute("SELECT 1 FROM settings WHERE k='boxes'").fetchone():
        old = {r[0]: r[1] for r in con.execute("SELECT k,v FROM settings WHERE k IN ('set3','set5')")}
        con.execute("INSERT INTO settings VALUES('boxes',?)",
                    (json.dumps({"3": int(old.get("set3") or 12000), "5": int(old.get("set5") or 20000)}),))
    con.execute("INSERT OR IGNORE INTO settings VALUES('kaspi_link','')")
    con.commit(); con.close()

init_db()

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()

def settings():
    s = dict(DEFAULTS)
    s.update({r["k"]: r["v"] for r in db().execute("SELECT k,v FROM settings")})
    return s

def set_prices(s):
    """Боксы: {"3": 12000, "5": 20000} — число флаконов и цена."""
    try:
        d = json.loads(s.get("boxes") or "{}")
    except ValueError:
        d = {}
    out = {}
    for k, v in d.items():
        try:
            n, pr = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if 2 <= n <= 30 and 1 <= pr <= 1_000_000:
            out[str(n)] = pr
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))

def lighten(hx, k=0.4):
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))

_hits = {}
def too_many(key, limit, window):
    now = time.time()
    lst = [t for t in _hits.get(key, []) if now - t < window]
    if len(lst) >= limit:
        _hits[key] = lst
        return True
    lst.append(now); _hits[key] = lst
    return False

# ---------- магазин ----------
@app.route("/")
def shop():
    s = settings()
    rows = db().execute("SELECT * FROM products WHERE active=1 ORDER BY sort,id").fetchall()
    media = defaultdict(list)
    for m in db().execute("SELECT * FROM product_media ORDER BY id"):
        media[m["product_id"]].append(m)
    products = []
    for r in rows:
        extra = [m for m in media[r["id"]] if m["kind"] == "img"]
        vid = next((m for m in media[r["id"]] if m["kind"] in ("video", "yt")), None)
        products.append(dict(id=r["id"], brand=r["brand"], name=r["name"], gender=r["gender"], price=r["price"],
                             img="/static/img/" + r["img"], note_ru=r["note"], note_kk=r["note_kk"] or r["note"],
                             note_en=r["note_en"] or r["note"],
                             desc_ru=r["desc_ru"] or "", desc_kk=r["desc_kk"] or "", desc_en=r["desc_en"] or "",
                             gallery=["/static/img/" + r["img"]] + ["/static/img/" + m["file"] for m in extra],
                             video=({"type": "yt", "src": vid["file"]} if vid and vid["kind"] == "yt" else
                                    {"type": "file", "src": "/static/video/" + vid["file"]} if vid else None)))
    w = "".join(c for c in s["whatsapp"] if c.isdigit())
    phone = f"+{w[0]} {w[1:4]} {w[4:7]} {w[7:]}" if len(w) == 11 else "+" + w
    texts = {}
    for l, _ in LANG_NAMES:
        for k, _ in TEXT_KEYS:
            if s.get(f"t_{l}_{k}"):
                texts.setdefault(l, {})[k] = s[f"t_{l}_{k}"]
    accent = s["accent"] if HEX.match(s["accent"]) else DEFAULTS["accent"]
    return render_template("shop.html", products=products, sets=set_prices(s), kaspi_link=s.get("kaspi_link", ""),
                           whatsapp=w, phone_display=phone, is_admin=bool(session.get("admin")), store_name=s["store_name"],
                           emblem=s["emblem"], accent=accent, accent2=lighten(accent), instagram=s["instagram"],
                           currency=s["currency"] or "₸", texts=texts)

@app.post("/api/orders")
def api_order():
    if too_many("order:" + (request.remote_addr or ""), 20, 3600):
        return jsonify(error="rate"), 429
    d = request.get_json(silent=True) or {}
    def txt(k, n):
        return str(d.get(k) or "").strip()[:n]
    name, phone, city, address, note = txt("name", 80), txt("phone", 30), txt("city", 80), txt("address", 200), txt("note", 300)
    delivery = d.get("delivery") if d.get("delivery") in ("ship", "pick") else "ship"
    pay = d.get("pay") if d.get("pay") in PAYS else "kaspi"
    lang = d.get("lang") if d.get("lang") in ("kk", "ru", "en") else "ru"
    if not name or len([c for c in phone if c.isdigit()]) < 10:
        return jsonify(error="contact"), 400
    if delivery == "ship" and (not city or not address):
        return jsonify(error="address"), 400
    con = db()
    prods = {r["id"]: r for r in con.execute("SELECT * FROM products WHERE active=1")}
    sp = set_prices(settings())
    lines, total = [], 0
    for it in (d.get("items") or [])[:50]:
        try:
            pid, ml = int(it["id"]), int(it["ml"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid in prods and 1 <= ml <= 1000:
            amt = prods[pid]["price"] * ml
            total += amt
            lines.append(("ml", pid, ml, amt, None, None))
    for st in (d.get("sets") or [])[:20]:
        try:
            n, picks = int(st["n"]), [int(x) for x in st["picks"]]
        except (KeyError, TypeError, ValueError):
            continue
        if str(n) in sp and len(picks) == n and all(p in prods for p in picks):
            total += sp[str(n)]
            lines.append(("set", None, 1, sp[str(n)], n, ",".join(map(str, picks))))
    if not lines:
        return jsonify(error="empty"), 400
    cur = con.execute("INSERT INTO orders(created,name,phone,city,address,delivery,pay,note,total,status,lang) "
                      "VALUES(?,?,?,?,?,?,?,?,?,'new',?)",
                      (now_kz().isoformat(timespec="seconds"), name, phone, city, address, delivery, pay, note, total, lang))
    con.executemany("INSERT INTO order_items(order_id,kind,product_id,qty,amount,set_n,picks) VALUES(?,?,?,?,?,?,?)",
                    [(cur.lastrowid,) + l for l in lines])
    con.commit()
    return jsonify(id=cur.lastrowid, total=total)

# ---------- админка ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]
app.jinja_env.globals.update(csrf_token=csrf_token, STATUSES=STATUSES, PAYS=PAYS, GENDERS=GENDERS)

@app.before_request
def csrf_protect():
    if request.method == "POST" and request.path.startswith("/admin"):
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400)

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

DUMMY_HASH = generate_password_hash("not-a-real-password")

def admin_row():
    return db().execute("SELECT * FROM admins WHERE id=1").fetchone()

def local_ips():
    ips = {"127.0.0.1", "::1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return ips

def is_local():
    """Запрос с этого же компьютера (в т.ч. по адресу вида 192.168.x.x своего ПК)."""
    return request.remote_addr in local_ips() and not request.headers.get("X-Forwarded-For")

def check_new(lg, pw, pw2):
    if not (3 <= len(lg) <= 40):
        return "Логин: от 3 до 40 символов."
    if len(pw) < 8:
        return "Пароль: минимум 8 символов."
    if pw != pw2:
        return "Пароли не совпадают."
    return ""

@app.route("/admin/setup", methods=["GET", "POST"])
def setup():
    if admin_row():
        return redirect(url_for("login"))
    need_key = not is_local()
    if need_key and not SETUP_KEY:
        return render_template("setup.html", blocked=True, need_key=True, err="")
    err = ""
    if request.method == "POST":
        lg, pw = request.form.get("login", "").strip(), request.form.get("password", "")
        if too_many("setup:" + (request.remote_addr or ""), 10, 600):
            err = "Слишком много попыток. Подождите 10 минут."
        elif need_key and not hmac.compare_digest(request.form.get("key", "").encode(), SETUP_KEY.encode()):
            err = "Неверный ключ настройки."
        else:
            err = check_new(lg, pw, request.form.get("password2", ""))
        if not err:
            db().execute("INSERT INTO admins VALUES(1,?,?)", (lg, generate_password_hash(pw))); db().commit()
            session.clear(); session["admin"] = True; csrf_token()
            return redirect(url_for("settings_page"))
    return render_template("setup.html", blocked=False, need_key=need_key, err=err)

@app.route("/admin/login", methods=["GET", "POST"])
def login():
    row = admin_row()
    if not row:
        return redirect(url_for("setup"))
    if session.get("admin"):
        return redirect(url_for("dashboard"))
    err = ""
    if request.method == "POST":
        if too_many("login:" + (request.remote_addr or ""), 5, 600):
            err = "Слишком много попыток. Подождите 10 минут."
        else:
            lg_ok = hmac.compare_digest(request.form.get("login", "").strip().lower().encode(), row["login"].lower().encode())
            pw_ok = check_password_hash(row["pw"] if lg_ok else DUMMY_HASH, request.form.get("password", ""))
            if lg_ok and pw_ok:
                session.clear(); session["admin"] = True; csrf_token()
                return redirect(url_for("dashboard"))
            err = "Неверный логин или пароль."
    return render_template("login.html", err=err)

@app.route("/admin/account", methods=["GET", "POST"])
@admin_required
def account():
    row = admin_row()
    msg = err = ""
    if request.method == "POST":
        lg, pw = request.form.get("login", "").strip(), request.form.get("password", "")
        if too_many("acct:" + (request.remote_addr or ""), 10, 600):
            err = "Слишком много попыток. Подождите 10 минут."
        elif not check_password_hash(row["pw"], request.form.get("current", "")):
            err = "Текущий пароль неверный."
        elif pw:
            err = check_new(lg, pw, request.form.get("password2", ""))
        elif not (3 <= len(lg) <= 40):
            err = "Логин: от 3 до 40 символов."
        if not err:
            db().execute("UPDATE admins SET login=?,pw=? WHERE id=1", (lg, generate_password_hash(pw) if pw else row["pw"]))
            db().commit(); row = admin_row(); msg = "Сохранено"
    return render_template("account.html", login=row["login"], msg=msg, err=err)

@app.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin")
@admin_required
def dashboard():
    def ok(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    frm, to = request.args.get("from", ""), request.args.get("to", "")
    frm, to = (frm if ok(frm) else ""), (to if ok(to) else "")
    where, args, days = "", [], request.args.get("days", "30")
    if frm or to:
        days = "custom"
        if frm:
            where += " AND o.created>=?"; args.append(frm + "T00:00:00")
        if to:
            where += " AND o.created<=?"; args.append(to + "T23:59:59")
    elif days in ("7", "30", "90"):
        where, args = " AND o.created>=?", [(now_kz() - timedelta(days=int(days))).isoformat(timespec="seconds")]
    else:
        days = "all"
    con = db()
    sold = ",".join("'%s'" % s for s in SOLD)
    opn = ",".join("'%s'" % s for s in OPEN)
    k = con.execute(f"SELECT COUNT(*) c, COALESCE(SUM(total),0) t FROM orders o WHERE status IN ({sold}){where}", args).fetchone()
    pend = con.execute(f"SELECT COUNT(*) c, COALESCE(SUM(total),0) t FROM orders o WHERE status IN ({opn}){where}", args).fetchone()
    cancelled = con.execute(f"SELECT COUNT(*) FROM orders o WHERE status='cancelled'{where}", args).fetchone()[0]
    ml_total = con.execute(f"SELECT COALESCE(SUM(i.qty),0) FROM order_items i JOIN orders o ON o.id=i.order_id "
                           f"WHERE i.kind='ml' AND o.status IN ({sold}){where}", args).fetchone()[0]
    top = con.execute(f"SELECT p.brand,p.name,SUM(i.qty) ml,SUM(i.amount) rev FROM order_items i JOIN orders o ON o.id=i.order_id "
                      f"JOIN products p ON p.id=i.product_id WHERE i.kind='ml' AND o.status IN ({sold}){where} "
                      f"GROUP BY p.id ORDER BY ml DESC", args).fetchall()
    gender = con.execute(f"SELECT p.gender g,SUM(i.qty) ml,SUM(i.amount) rev FROM order_items i JOIN orders o ON o.id=i.order_id "
                         f"JOIN products p ON p.id=i.product_id WHERE i.kind='ml' AND o.status IN ({sold}){where} GROUP BY p.gender", args).fetchall()
    pays = con.execute(f"SELECT pay,COUNT(*) c,SUM(total) t FROM orders o WHERE status IN ({sold}){where} GROUP BY pay", args).fetchall()
    bylang = con.execute(f"SELECT COALESCE(lang,'ru') l,COUNT(*) c,SUM(total) t FROM orders o WHERE status IN ({sold}){where} GROUP BY l", args).fetchall()
    stat = {r["status"]: r["c"] for r in con.execute(f"SELECT status,COUNT(*) c FROM orders o WHERE 1=1{where} GROUP BY status", args)}
    names = {r["id"]: f'{r["brand"]} {r["name"]}' for r in con.execute("SELECT id,brand,name FROM products")}
    set_rows, pick_cnt = defaultdict(lambda: [0, 0]), defaultdict(int)
    for r in con.execute(f"SELECT i.set_n,i.amount,i.picks FROM order_items i JOIN orders o ON o.id=i.order_id "
                         f"WHERE i.kind='set' AND o.status IN ({sold}){where}", args):
        set_rows[r["set_n"]][0] += 1; set_rows[r["set_n"]][1] += r["amount"]
        for pid in (r["picks"] or "").split(","):
            if pid.isdigit():
                pick_cnt[int(pid)] += 1
    picks = sorted(((names.get(p, "?"), c) for p, c in pick_cnt.items()), key=lambda x: -x[1])
    daily = {r["d"]: r["t"] for r in con.execute(f"SELECT substr(o.created,1,10) d,SUM(o.total) t FROM orders o "
                                                 f"WHERE o.status IN ({sold}){where} GROUP BY d", args)}
    end = datetime.strptime(to, "%Y-%m-%d").date() if to else now_kz().date()
    n = 14
    if frm:
        n = min(31, max(1, (end - datetime.strptime(frm, "%Y-%m-%d").date()).days + 1))
    series = [((end - timedelta(days=i)).isoformat(), daily.get((end - timedelta(days=i)).isoformat(), 0)) for i in range(n - 1, -1, -1)]
    return render_template("dashboard.html", days=days, frm=frm, to=to, k=k, pend=pend, cancelled=cancelled, ml_total=ml_total,
                           top=top, gender=gender, pays=pays, bylang=bylang, stat=stat, sets_sold=sorted(set_rows.items()),
                           picks=picks, series=series)

def order_lines(con, ids):
    names = {r["id"]: f'{r["brand"]} {r["name"]}' for r in con.execute("SELECT id,brand,name FROM products")}
    out = defaultdict(list)
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    for r in con.execute(f"SELECT * FROM order_items WHERE order_id IN ({q}) ORDER BY id", ids):
        if r["kind"] == "ml":
            out[r["order_id"]].append(f'{names.get(r["product_id"], "?")} — {r["qty"]} мл')
        else:
            ps = ", ".join(names.get(int(p), "?") for p in (r["picks"] or "").split(",") if p.isdigit())
            out[r["order_id"]].append(f'Набор на {r["set_n"]}: {ps}')
    return out

@app.route("/admin/orders")
@admin_required
def orders():
    st = request.args.get("status", "")
    con = db()
    if st in dict(STATUSES):
        rows = con.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 300", (st,)).fetchall()
    else:
        st, rows = "", con.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 300").fetchall()
    return render_template("orders.html", rows=rows, st=st, lines=order_lines(con, [r["id"] for r in rows]))

@app.post("/admin/orders/<int:oid>/status")
@admin_required
def order_status(oid):
    st = request.form.get("status")
    if st in dict(STATUSES):
        db().execute("UPDATE orders SET status=? WHERE id=?", (st, oid)); db().commit()
    return redirect(request.referrer or url_for("orders"))

def safe(v):
    v = str(v)
    return "'" + v if v[:1] in "=+-@" else v

@app.route("/admin/export.csv")
@admin_required
def export():
    con = db()
    rows = con.execute("SELECT * FROM orders ORDER BY id").fetchall()
    lines = order_lines(con, [r["id"] for r in rows])
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["№", "Дата", "Имя", "Телефон", "Город", "Адрес", "Получение", "Оплата", "Статус", "Сумма", "Состав", "Язык"])
    sn = dict(STATUSES)
    for r in rows:
        w.writerow([r["id"], r["created"], safe(r["name"]), safe(r["phone"]), safe(r["city"]), safe(r["address"]),
                    r["delivery"], PAYS.get(r["pay"], r["pay"]), sn.get(r["status"], r["status"]), r["total"], safe("; ".join(lines[r["id"]])), r["lang"] or ""])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})

def to_price(v, default):
    try:
        n = int(v)
        return n if 1 <= n <= 1_000_000 else default
    except (TypeError, ValueError):
        return default

def save_image(f):
    """Проверяет и сохраняет фото. Возвращает имя файла или пустую строку."""
    if not f or not f.filename:
        return ""
    head = f.read(12); f.seek(0)
    ext = ("jpg" if head[:3] == b"\xff\xd8\xff" else "png" if head[:4] == b"\x89PNG" else
           "webp" if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else "")
    if not ext:
        return ""
    fn = f"p{int(time.time())}_{secrets.token_hex(3)}.{ext}"
    path = os.path.join(BASE, "static", "img", fn)
    if Image:
        try:
            im = Image.open(f); im.load()
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (0, 0, 0)); bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3]); im = bg
            im.thumbnail((1000, 1000))
            fn = fn.rsplit(".", 1)[0] + ".jpg"; path = os.path.join(BASE, "static", "img", fn)
            im.convert("RGB").save(path, "JPEG", quality=86, optimize=True)
            return fn
        except Exception:
            return ""
    f.save(path)
    return fn

def drop_image(fn):
    if fn and not db().execute("SELECT 1 FROM products WHERE img=?", (fn,)).fetchone():
        try:
            os.remove(os.path.join(BASE, "static", "img", os.path.basename(fn)))
        except OSError:
            pass

def save_video(f):
    """Принимает mp4 или webm. Возвращает имя файла или пустую строку."""
    if not f or not f.filename:
        return ""
    head = f.read(12); f.seek(0)
    ext = "mp4" if head[4:8] == b"ftyp" else "webm" if head[:4] == b"\x1aE\xdf\xa3" else ""
    if not ext:
        return ""
    folder = os.path.join(BASE, "static", "video")
    os.makedirs(folder, exist_ok=True)
    fn = f"v{int(time.time())}_{secrets.token_hex(3)}.{ext}"
    f.save(os.path.join(folder, fn))
    return fn

def youtube_embed(url):
    m = YT_RE.search(url or "")
    return f"https://www.youtube.com/embed/{m.group(1)}" if m else ""

def remove_media_file(m):
    if m["kind"] == "img":
        path = os.path.join(BASE, "static", "img", os.path.basename(m["file"]))
    elif m["kind"] == "video":
        path = os.path.join(BASE, "static", "video", os.path.basename(m["file"]))
    else:
        return
    try:
        os.remove(path)
    except OSError:
        pass

def save_media(pid):
    """Доп. фото, удаление отмеченных, видео (файл или ссылка YouTube)."""
    con, f = db(), request.form
    for mid in f.getlist("del_media"):
        if mid.isdigit():
            m = con.execute("SELECT * FROM product_media WHERE id=? AND product_id=?", (int(mid), pid)).fetchone()
            if m:
                remove_media_file(m)
                con.execute("DELETE FROM product_media WHERE id=?", (m["id"],))
    cnt = con.execute("SELECT COUNT(*) FROM product_media WHERE product_id=? AND kind='img'", (pid,)).fetchone()[0]
    bad = 0
    for img in request.files.getlist("images"):
        if not img or not img.filename:
            continue
        if cnt >= MAX_EXTRA_PHOTOS:
            flash(f"Дополнительных фото максимум {MAX_EXTRA_PHOTOS}, лишние не добавлены")
            break
        fn = save_image(img)
        if fn:
            con.execute("INSERT INTO product_media(product_id,kind,file) VALUES(?,?,?)", (pid, "img", fn)); cnt += 1
        else:
            bad += 1
    if bad:
        flash(f"Не загружено фото: {bad} (нужны jpg, png или webp)")
    new = None
    vf, vurl = request.files.get("video"), f.get("video_url", "").strip()
    if vf and vf.filename:
        fn = save_video(vf)
        new = ("video", fn) if fn else None
        if not fn:
            flash("Видео: нужен файл mp4 или webm")
    elif vurl:
        emb = youtube_embed(vurl)
        new = ("yt", emb) if emb else None
        if not emb:
            flash("Видео: вставьте ссылку на YouTube")
    if new or f.get("del_video"):
        for m in con.execute("SELECT * FROM product_media WHERE product_id=? AND kind IN ('video','yt')", (pid,)).fetchall():
            remove_media_file(m)
            con.execute("DELETE FROM product_media WHERE id=?", (m["id"],))
    if new:
        con.execute("INSERT INTO product_media(product_id,kind,file) VALUES(?,?,?)", (pid, new[0], new[1]))
    con.commit()

@app.errorhandler(413)
def too_big(_):
    return "Файл слишком большой (максимум 64 МБ). Вернитесь назад: фото поменьше или видео лучше добавить ссылкой на YouTube.", 413

@app.route("/admin/products")
@admin_required
def products():
    return render_template("products.html", rows=db().execute("SELECT * FROM products ORDER BY sort,id").fetchall())

@app.post("/admin/products/save")
@admin_required
def products_save():
    con = db()
    for r in con.execute("SELECT id,price,sort FROM products").fetchall():
        con.execute("UPDATE products SET price=?,sort=?,active=? WHERE id=?",
                    (to_price(request.form.get(f"price_{r['id']}"), r["price"]), to_price(request.form.get(f"sort_{r['id']}"), r["sort"] or r["id"]),
                     1 if request.form.get(f"active_{r['id']}") else 0, r["id"]))
    con.commit(); flash("Сохранено")
    return redirect(url_for("products"))

def product_fields():
    f = request.form
    gender = f.get("gender") if f.get("gender") in GENDERS else "unisex"
    return (f.get("brand", "").strip()[:60], f.get("name", "").strip()[:100], gender, to_price(f.get("price"), 600),
            f.get("note", "").strip()[:120], f.get("note_kk", "").strip()[:120] or None, f.get("note_en", "").strip()[:120] or None)

def save_desc(pid):
    f = request.form
    db().execute("UPDATE products SET desc_ru=?,desc_kk=?,desc_en=? WHERE id=?",
                 (f.get("desc_ru", "").strip()[:1500] or None, f.get("desc_kk", "").strip()[:1500] or None,
                  f.get("desc_en", "").strip()[:1500] or None, pid))
    db().commit()

@app.post("/admin/products/add")
@admin_required
def products_add():
    brand, name, gender, price, note, kk, en = product_fields()
    fn = save_image(request.files.get("image"))
    if not brand or not name or not fn:
        flash("Заполните бренд, название и загрузите фото (jpg, png или webp)")
        return redirect(url_for("products"))
    nxt = (db().execute("SELECT COALESCE(MAX(sort),0) FROM products").fetchone()[0] or 0) + 1
    cur = db().execute("INSERT INTO products(brand,name,gender,price,img,note,note_kk,note_en,active,sort) VALUES(?,?,?,?,?,?,?,?,1,?)",
                       (brand, name, gender, price, fn, note, kk, en, nxt))
    db().commit()
    save_desc(cur.lastrowid); save_media(cur.lastrowid); flash("Товар добавлен")
    return redirect(url_for("products"))

@app.route("/admin/products/<int:pid>", methods=["GET", "POST"])
@admin_required
def product_edit(pid):
    con = db()
    r = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        abort(404)
    if request.method == "POST":
        brand, name, gender, price, note, kk, en = product_fields()
        if not brand or not name:
            flash("Бренд и название обязательны")
        else:
            fn = save_image(request.files.get("image")) or r["img"]
            con.execute("UPDATE products SET brand=?,name=?,gender=?,price=?,note=?,note_kk=?,note_en=?,img=? WHERE id=?",
                        (brand, name, gender, price, note, kk, en, fn, pid))
            con.commit()
            if fn != r["img"]:
                drop_image(r["img"])
            save_desc(pid); save_media(pid)
            flash("Сохранено")
            return redirect(url_for("product_edit", pid=pid))
    media = con.execute("SELECT * FROM product_media WHERE product_id=? ORDER BY id", (pid,)).fetchall()
    return render_template("product_edit.html", r=r, extra=[m for m in media if m["kind"] == "img"],
                           video=next((m for m in media if m["kind"] in ("video", "yt")), None))

@app.post("/admin/products/<int:pid>/delete")
@admin_required
def product_delete(pid):
    con = db()
    r = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        abort(404)
    if con.execute("SELECT 1 FROM order_items WHERE product_id=? OR (kind='set' AND (',' || picks || ',') LIKE ?)",
                   (pid, f"%,{pid},%")).fetchone():
        flash("У этого товара есть заказы, поэтому удалить его нельзя. Снимите галочку «Виден в магазине», и он пропадёт с сайта.")
        return redirect(url_for("product_edit", pid=pid))
    for m in con.execute("SELECT * FROM product_media WHERE product_id=?", (pid,)).fetchall():
        remove_media_file(m)
    con.execute("DELETE FROM product_media WHERE product_id=?", (pid,))
    con.execute("DELETE FROM products WHERE id=?", (pid,)); con.commit()
    drop_image(r["img"]); flash("Товар удалён")
    return redirect(url_for("products"))

@app.route("/admin/settings")
@admin_required
def settings_page():
    s = settings()
    boxes = list(set_prices(s).items())
    rows = boxes + [("", "")] * min(2, 8 - len(boxes))
    return render_template("settings.html", s=s, rows=rows, DEFAULT_TEXTS=DEFAULT_TEXTS, TEXT_KEYS=TEXT_KEYS, LANG_NAMES=LANG_NAMES)

@app.post("/admin/settings/save")
@admin_required
def settings_save():
    f, con, s = request.form, db(), settings()
    def put(k, v):
        con.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    put("store_name", f.get("store_name", "").strip()[:40] or s["store_name"])
    put("emblem", f.get("emblem", "").strip()[:3])
    put("accent", f.get("accent") if HEX.match(f.get("accent", "")) else s["accent"])
    wa = "".join(c for c in f.get("whatsapp", "") if c.isdigit())
    if 10 <= len(wa) <= 15:
        put("whatsapp", wa)
    else:
        flash("WhatsApp: введите номер полностью, например 77051511150. Номер не изменён.")
    ig = f.get("instagram", "").strip().rstrip("/").split("/")[-1].lstrip("@")
    if not ig or re.fullmatch(r"[A-Za-z0-9._]{1,30}", ig):
        put("instagram", ig)
    else:
        flash("Instagram: неверное имя профиля. Оно не изменено.")
    link = f.get("kaspi_link", "").strip()
    put("kaspi_link", link if link.startswith("https://") else "")
    put("currency", f.get("currency", "").strip()[:4] or "₸")
    boxes = {}
    for i in range(8):
        try:
            n, pr = int(f.get(f"bn_{i}", "")), int(f.get(f"bp_{i}", ""))
        except ValueError:
            continue
        if 2 <= n <= 30 and 1 <= pr <= 1_000_000:
            boxes[str(n)] = pr
    put("boxes", json.dumps(dict(sorted(boxes.items(), key=lambda kv: int(kv[0])))))
    for l, _ in LANG_NAMES:
        for k, _ in TEXT_KEYS:
            v = f.get(f"t_{l}_{k}", "").strip()[:300]
            if v:
                put(f"t_{l}_{k}", v)
            else:
                con.execute("DELETE FROM settings WHERE k=?", (f"t_{l}_{k}",))
    con.commit(); flash("Настройки сохранены")
    return redirect(url_for("settings_page"))

if __name__ == "__main__":
    import sys
    if "--reset-admin" in sys.argv:   # забыли пароль: python app.py --reset-admin
        con = sqlite3.connect(DB_PATH); con.execute("DELETE FROM admins"); con.commit(); con.close()
        print("Админ удалён. Откройте /admin и создайте новый логин и пароль.")
        sys.exit(0)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
