"""Выгрузка каталога из shop.db в папку seed/ — чтобы сайт на бесплатном хостинге
сам получал товары при каждом запуске.

Запуск (в папке, где лежат app.py и shop.db):   python export_catalog.py
Потом:  git add .   git commit -m "Обновил каталог"   git push

В выгрузку попадают: товары, дополнительные фото, видео-ссылки YouTube, настройки магазина.
Заказы, телефоны клиентов и пароль админа НЕ выгружаются.
"""
import json, os, shutil, sqlite3, sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "shop.db")
if not os.path.exists(DB):
    sys.exit(f"Не найден файл базы: {DB}")

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

products = [dict(r) for r in con.execute("SELECT * FROM products ORDER BY COALESCE(sort,id), id")]
media, skipped = [], 0
if "product_media" in tables:
    for r in con.execute("SELECT product_id,kind,file FROM product_media ORDER BY id"):
        if r["kind"] == "video":      # видеофайлы тяжёлые, в GitHub их не кладём — используйте ссылку YouTube
            skipped += 1
            continue
        media.append(dict(r))
settings = {r["k"]: r["v"] for r in con.execute("SELECT k,v FROM settings")} if "settings" in tables else {}
con.close()

seed_dir, img_dir = os.path.join(BASE, "seed"), os.path.join(BASE, "seed", "img")
shutil.rmtree(img_dir, ignore_errors=True)
os.makedirs(img_dir)
files = {p["img"] for p in products if p.get("img")} | {m["file"] for m in media if m["kind"] == "img"}
missing = []
for fn in sorted(files):
    src = os.path.join(BASE, "static", "img", os.path.basename(fn))
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(img_dir, os.path.basename(fn)))
    else:
        missing.append(fn)

with open(os.path.join(seed_dir, "catalog.json"), "w", encoding="utf-8") as fh:
    json.dump({"products": products, "media": media, "settings": settings}, fh, ensure_ascii=False, indent=1)

print(f"Готово: товаров {len(products)}, доп. фото {sum(m['kind']=='img' for m in media)}, "
      f"видео-ссылок {sum(m['kind']=='yt' for m in media)}, картинок скопировано {len(files)-len(missing)}.")
if skipped:
    print(f"Внимание: {skipped} видео-файл(ов) пропущено (слишком тяжёлые). Замените их ссылкой на YouTube в админке и запустите скрипт снова.")
if missing:
    print("Не найдены картинки:", ", ".join(missing))
print("Дальше: git add .  →  git commit -m \"Обновил каталог\"  →  git push")
