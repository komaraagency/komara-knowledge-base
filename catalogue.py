# ---------------------------------------------------------------------------
# Catalogue + panier 100% locaux — Komara Agency 🇬🇳
# Tables : products, cart. Zéro IA externe, zéro dépendance réseau.
# ---------------------------------------------------------------------------

import re
import sqlite3
import threading
from datetime import datetime, timezone

DB_CONN = None
DB_LOCK = threading.Lock()


def init_catalogue_db(conn: sqlite3.Connection) -> None:
    """Crée les tables produits et panier (appelé par actions.init_db)."""
    global DB_CONN
    DB_CONN = conn
    with DB_LOCK:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price REAL NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cart (
                chat_id TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER DEFAULT 1,
                added_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, product_id)
            );
        """)
        n = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if n == 0:
            now = _now()
            conn.executemany(
                "INSERT INTO products (category, name, description, price, active, created_at)"
                " VALUES (?,?,?,?,1,?)",
                [
                    (cat, name, desc, price, now)
                    for cat, name, desc, price in [
                        ("Logo", "Logo professionnel", "Identité visuelle unique, 2 propositions, fichiers HD", 80.0),
                        ("Logo", "Logo + charte graphique", "Logo complet + couleurs + polices + carte de visite", 150.0),
                        ("Site web", "Site vitrine", "Site responsive 5 pages, SEO de base, formulaire", 300.0),
                        ("Site web", "Site e-commerce", "Boutique avec panier et paiement en ligne", 500.0),
                        ("Agent IA", "Agent WhatsApp", "Assistant qui répond, qualifie et vend 24/7", 300.0),
                        ("Agent IA", "Agent multi-canal", "WhatsApp + Instagram + Telegram connectés", 450.0),
                        ("Visuels", "Pack visuels (10)", "10 visuels réseaux sociaux prêts à publier", 30.0),
                        ("Visuels", "Vidéo animée IA", "Courte vidéo marketing pour tes campagnes", 80.0),
                        ("Formation", "Formation IA (2h)", "Prise en main des outils IA pour ton business", 50.0),
                    ]
                ],
            )
            conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Textes multilingues
# ---------------------------------------------------------------------------

TT: dict[str, dict[str, str]] = {
    "fr": {
        "catalogue_head": "🛍️ Catalogue Komara Agency 🇬🇳\n\n{items}\n\n➕ Ajouter au panier : tape 'ajouter <numéro>'\n🛒 Voir le panier : tape 'panier'",
        "catalogue_item": "{n}. {name} — {price:g}€\n   {desc}",
        "catalogue_empty": "🛍️ Le catalogue arrive très bientôt. En attendant, tape 'devis' pour un projet sur mesure 👇",
        "added": "✅ {name} ajouté au panier (x{qty})\n🛒 Panier : {count} article(s) — {total:g}€\nTape 'panier' pour voir, 'commander' pour finaliser 🚀",
        "not_found": "Je n'ai pas trouvé ce numéro. Tape 'catalogue' pour revoir la liste 🙏",
        "cart_head": "🛒 Ton panier :\n\n{items}\n\n➡️ Total : {total:g}€\n\n'commander' pour finaliser • 'vider' pour annuler",
        "cart_line": "• {name} x{qty} — {line:g}€",
        "cart_empty": "🛒 Ton panier est vide. Tape 'catalogue' pour voir les offres 👀",
        "cleared": "🛒 Panier vidé. Tape 'catalogue' quand tu veux 🙏",
        "checkout_start": "🚀 On finalise ta commande :\n\n{items}\n\n➡️ Total : {total:g}€\n\n📍 Comment te livrer ? (ville, quartier ou lien de collecte)",
        "checkout_known_name": "📍 C'est pour {name} ? (ok pour confirmer, ou écris le bon nom)",
        "checkout_name": "👤 C'est pour quel nom ?",
        "checkout_confirm": "✅ Récapitulatif :\n\n{items}\n➡️ Total : {total:g}€\n📍 Livraison : {adresse}\n\nTape 'confirmer' pour valider, 'annuler' pour arrêter.",
        "checkout_done": "🎉 Commande n°{oid} enregistrée !\n\nTotal : {total:g}€\nL'équipe te contacte pour finaliser le paiement et la livraison sous 24h.\nMerci pour ta confiance 🇬🇳\nUne question ? WhatsApp {whatsapp}",
        "prod_added": "✅ Produit ajouté (id {pid}) : {name} — {price:g}€",
        "prod_deleted": "🗑️ Produit {pid} retiré du catalogue",
        "prod_price_set": "✅ Produit {pid} : nouveau prix {price:g}€",
        "prod_list": "🛍️ Catalogue (admin) :\n\n{items}",
        "prod_usage": "Usage : /produit add <catégorie>|<nom>|<description>|<prix>\n/produit del <id> • /produit maj <id>|<prix> • /produits",
    },
    "en": {
        "catalogue_head": "🛍️ Komara Agency Catalogue 🇬🇳\n\n{items}\n\n➕ Add to cart: type 'add <number>'\n🛒 View cart: type 'cart'",
        "catalogue_item": "{n}. {name} — {price:g}€\n   {desc}",
        "catalogue_empty": "🛍️ The catalogue is coming very soon. Meanwhile, type 'quote' for a custom project 👇",
        "added": "✅ {name} added to your cart (x{qty})\n🛒 Cart: {count} item(s) — {total:g}€\nType 'cart' to view, 'order' to checkout 🚀",
        "not_found": "I couldn't find that number. Type 'catalogue' to see the list again 🙏",
        "cart_head": "🛒 Your cart:\n\n{items}\n\n➡️ Total: {total:g}€\n\n'order' to checkout • 'clear' to empty",
        "cart_line": "• {name} x{qty} — {line:g}€",
        "cart_empty": "🛒 Your cart is empty. Type 'catalogue' to see the offers 👀",
        "cleared": "🛒 Cart cleared. Type 'catalogue' anytime 🙏",
        "checkout_start": "🚀 Let's finalize your order:\n\n{items}\n\n➡️ Total: {total:g}€\n\n📍 Delivery details? (city, area or pickup link)",
        "checkout_known_name": "📍 Is it for {name}? (ok to confirm, or type the right name)",
        "checkout_name": "👤 What name is it for?",
        "checkout_confirm": "✅ Summary:\n\n{items}\n➡️ Total: {total:g}€\n📍 Delivery: {adresse}\n\nType 'confirm' to validate, 'cancel' to stop.",
        "checkout_done": "🎉 Order #{oid} registered!\n\nTotal: {total:g}€\nThe team contacts you within 24h for payment and delivery.\nThank you for your trust 🇬🇳\nA question? WhatsApp {whatsapp}",
        "prod_added": "✅ Product added (id {pid}): {name} — {price:g}€",
        "prod_deleted": "🗑️ Product {pid} removed",
        "prod_price_set": "✅ Product {pid}: new price {price:g}€",
        "prod_list": "🛍️ Catalogue (admin):\n\n{items}",
        "prod_usage": "Usage: /produit add <category>|<name>|<description>|<price>\n/produit del <id> • /produit maj <id>|<price> • /produits",
    },
    "es": {
        "catalogue_head": "🛍️ Catálogo Komara Agency 🇬🇳\n\n{items}\n\n➕ Añadir: escribe 'añadir <número>'\n🛒 Ver carrito: 'carrito'",
        "catalogue_item": "{n}. {name} — {price:g}€\n   {desc}",
        "catalogue_empty": "🛍️ El catálogo llega muy pronto. Mientras tanto, escribe 'presupuesto' para un proyecto a medida 👇",
        "added": "✅ {name} añadido (x{qty})\n🛒 Carrito: {count} artículo(s) — {total:g}€\nEscribe 'carrito' para ver, 'ordenar' para finalizar 🚀",
        "not_found": "No encontré ese número. Escribe 'catálogo' para ver la lista 🙏",
        "cart_head": "🛒 Tu carrito:\n\n{items}\n\n➡️ Total: {total:g}€\n\n'ordenar' para finalizar • 'vaciar' para cancelar",
        "cart_line": "• {name} x{qty} — {line:g}€",
        "cart_empty": "🛒 Tu carrito está vacío. Escribe 'catálogo' para ver las ofertas 👀",
        "cleared": "🛒 Carrito vaciado 🙏",
        "checkout_start": "🚀 Finalizamos tu pedido:\n\n{items}\n\n➡️ Total: {total:g}€\n\n📍 ¿Entrega? (ciudad, zona o enlace de recogida)",
        "checkout_known_name": "📍 ¿Es para {name}? (ok para confirmar o escribe el nombre)",
        "checkout_name": "👤 ¿A qué nombre?",
        "checkout_confirm": "✅ Resumen:\n\n{items}\n➡️ Total: {total:g}€\n📍 Entrega: {adresse}\n\nEscribe 'confirmar' para validar, 'cancelar' para parar.",
        "checkout_done": "🎉 Pedido n°{oid} registrado.\n\nTotal: {total:g}€\nEl equipo te contacta en 24h para el pago y la entrega.\nGracias por tu confianza 🇬🇳\n¿Una pregunta? WhatsApp {whatsapp}",
        "prod_added": "✅ Producto añadido (id {pid}): {name} — {price:g}€",
        "prod_deleted": "🗑️ Producto {pid} eliminado",
        "prod_price_set": "✅ Producto {pid}: nuevo precio {price:g}€",
        "prod_list": "🛍️ Catálogo (admin):\n\n{items}",
        "prod_usage": "Uso: /produit add <categoría>|<nombre>|<descripción>|<precio>\n/produit del <id> • /produit maj <id>|<precio> • /produits",
    },
    "ar": {
        "catalogue_head": "🛍️ كتالوج Komara Agency 🇬🇳\n\n{items}\n\n➕ للإضافة: اكتب 'أضف <رقم>'\n🛒 للسلة: 'سلة'",
        "catalogue_item": "{n}. {name} — {price:g}€\n   {desc}",
        "catalogue_empty": "🛍️ الكتالوج قادم قريبا. في انتظاره اكتب 'عرض سعر' لمشروع خاص 👇",
        "added": "✅ تمت إضافة {name} (x{qty})\n🛒 السلة: {count} عنصر — {total:g}€\nاكتب 'سلة' للعرض، 'طلب' للنهائية 🚀",
        "not_found": "لم أجد هذا الرقم. اكتب 'كتالوج' لإعادة القائمة 🙏",
        "cart_head": "🛒 سلتك:\n\n{items}\n\n➡️ المجموع: {total:g}€\n\n'طلب' للنهائية • 'تفريغ' للإلغاء",
        "cart_line": "• {name} x{qty} — {line:g}€",
        "cart_empty": "🛒 سلتك فارغة. اكتب 'كتالوج' لرؤية العروض 👀",
        "cleared": "🛒 تم تفريغ السلة 🙏",
        "checkout_start": "🚀 ننهي طلبك:\n\n{items}\n\n➡️ المجموع: {total:g}€\n\n📍 التسليم؟ (المدينة أو الحي أو رابط الاستلام)",
        "checkout_known_name": "📍 باسم {name}؟ (اكتب موافق أو الاسم الصحيح)",
        "checkout_name": "👤 باسم من؟",
        "checkout_confirm": "✅ الملخص:\n\n{items}\n➡️ المجموع: {total:g}€\n📍 التسليم: {adresse}\n\nاكتب 'تأكيد' للتحقيق أو 'إلغاء' للتوقف.",
        "checkout_done": "🎉 تم تسجيل الطلب رقم {oid}!\n\nالمجموع: {total:g}€\nيتصل بك الفريق خلال 24 ساعة للدفع والتسليم.\nشكرا لثقتك 🇬🇳\nسؤال؟ واتساب {whatsapp}",
        "prod_added": "✅ أضيف المنتج (id {pid}): {name} — {price:g}€",
        "prod_deleted": "🗑️ حذف المنتج {pid}",
        "prod_price_set": "✅ المنتج {pid}: سعر جديد {price:g}€",
        "prod_list": "🛍️ الكتالوج (أدمن):\n\n{items}",
        "prod_usage": "الاستعمال: /produit add <فئة>|<اسم>|<وصف>|<سعر>\n/produit del <id> • /produit maj <id>|<سعر> • /produits",
    },
}


def tt(lang: str, key: str, **kw) -> str:
    base = TT.get(lang, TT["fr"]).get(key, TT["fr"].get(key, key))
    return base.format(**kw) if kw else base


# ---------------------------------------------------------------------------
# Déclencheurs
# ---------------------------------------------------------------------------

CATALOGUE_TRIGGERS = {
    "catalogue", "catalog", "catálogo", "catalogo", "كتالوج",
    "produits", "products", "shop", "boutique", "tienda", "متجر",
    "/catalogue", "/shop",
}
CART_TRIGGERS = {"panier", "cart", "carrito", "سلة", "/panier", "/cart"}
CLEAR_TRIGGERS = {"vider", "clear", "vaciar", "تفريغ", "vide panier"}
ADD_RE = re.compile(r"^(?:ajouter|add|añadir|anadir|أضف)\s+(\d+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers base
# ---------------------------------------------------------------------------

def active_products() -> list:
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT id, name, description, price FROM products"
            " WHERE active = 1 ORDER BY category, price"
        ).fetchall()
    return rows


def cart_items(chat_id: int) -> list:
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT p.name, c.qty, p.price FROM cart c"
            " JOIN products p ON p.id = c.product_id"
            " WHERE c.chat_id = ? ORDER BY c.added_at",
            (str(chat_id),),
        ).fetchall()
    return [(name, qty, qty * price) for name, qty, price in rows]


def cart_total(chat_id: int) -> float:
    return sum(line for _n, _q, line in cart_items(chat_id))


def cart_count(chat_id: int) -> int:
    with DB_LOCK:
        n = DB_CONN.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM cart WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()[0]
    return int(n)


def clear_cart(chat_id: int) -> None:
    with DB_LOCK:
        DB_CONN.execute("DELETE FROM cart WHERE chat_id = ?", (str(chat_id),))
        DB_CONN.commit()


def _cart_lines(chat_id: int, lang: str) -> str:
    return "\n".join(
        tt(lang, "cart_line", name=name, qty=qty, line=line)
        for name, qty, line in cart_items(chat_id)
    )


# ---------------------------------------------------------------------------
# Commandes client
# ---------------------------------------------------------------------------

def show_catalogue(bot, chat_id: int, lang: str) -> None:
    products = active_products()
    if not products:
        bot.send_message(chat_id, tt(lang, "catalogue_empty"))
        return
    items = "\n".join(
        tt(lang, "catalogue_item", n=i, name=name, price=price, desc=desc[:60])
        for i, (_pid, name, desc, price) in enumerate(products, start=1)
    )
    bot.send_message(chat_id, tt(lang, "catalogue_head", items=items))


def cmd_add(bot, chat_id: int, num: int, lang: str) -> None:
    products = active_products()
    if num < 1 or num > len(products):
        bot.send_message(chat_id, tt(lang, "not_found"))
        return
    pid, name, _d, price = products[num - 1]
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT INTO cart (chat_id, product_id, qty, added_at) VALUES (?,?,1,?)"
            " ON CONFLICT(chat_id, product_id) DO UPDATE SET qty = qty + 1",
            (str(chat_id), pid, _now()),
        )
        DB_CONN.commit()
    bot.send_message(
        chat_id,
        tt(lang, "added", name=name, qty=1, count=cart_count(chat_id),
           total=cart_total(chat_id)),
    )


def show_cart(bot, chat_id: int, lang: str) -> None:
    if cart_count(chat_id) == 0:
        bot.send_message(chat_id, tt(lang, "cart_empty"))
        return
    bot.send_message(
        chat_id,
        tt(lang, "cart_head", items=_cart_lines(chat_id, lang),
           total=cart_total(chat_id)),
    )


def handle_client(bot, chat_id: int, text: str, lang: str) -> bool:
    """Route les commandes catalogue/panier. True si le message est consommé."""
    low = text.strip().lower()
    if low in CATALOGUE_TRIGGERS:
        show_catalogue(bot, chat_id, lang)
        return True
    if low in CART_TRIGGERS:
        show_cart(bot, chat_id, lang)
        return True
    if low in CLEAR_TRIGGERS:
        clear_cart(chat_id)
        bot.send_message(chat_id, tt(lang, "cleared"))
        return True
    m = ADD_RE.match(low)
    if m:
        cmd_add(bot, chat_id, int(m.group(1)), lang)
        return True
    return False


# ---------------------------------------------------------------------------
# Checkout : panier → commande
# ---------------------------------------------------------------------------

def start_checkout(bot, chat_id: int, lang: str) -> bool:
    """"commander" avec un panier non vide → tunnel panier."""
    if cart_count(chat_id) == 0:
        return False
    import actions
    actions._save_flow(chat_id, "checkout", "adresse", {})
    bot.send_message(
        chat_id,
        tt(lang, "checkout_start", items=_cart_lines(chat_id, lang),
           total=cart_total(chat_id)),
    )
    return True


def step_checkout(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    import actions
    low = text.strip().lower()

    if step == "adresse":
        data["adresse"] = text[:200]
        actions._save_flow(chat_id, "checkout", "name", data)
        client = actions.get_client(chat_id)
        if client and client["name"]:
            bot.send_message(chat_id, tt(lang, "checkout_known_name", name=client["name"]))
        else:
            bot.send_message(chat_id, tt(lang, "checkout_name"))
        return True

    if step == "name":
        if low in actions.OK_WORDS:
            known = actions.get_client(chat_id)
            if known and known["name"]:
                data["name"] = known["name"]
            else:
                bot.send_message(chat_id, tt(lang, "checkout_name"))
                return True
        else:
            data["name"] = text[:100]
        actions._save_flow(chat_id, "checkout", "confirm", data)
        bot.send_message(
            chat_id,
            tt(lang, "checkout_confirm", items=_cart_lines(chat_id, lang),
               total=cart_total(chat_id), adresse=data.get("adresse", "")),
        )
        return True

    if step == "confirm":
        confirm_words = {"confirmer", "confirm", "confirmar", "تأكيد", "valider", "validar"} | actions.OK_WORDS
        if low not in confirm_words:
            bot.send_message(chat_id, tt(lang, "checkout_confirm",
                items=_cart_lines(chat_id, lang), total=cart_total(chat_id),
                adresse=data.get("adresse", "")))
            return True
        total = cart_total(chat_id)
        order_id = actions._insert("orders", {
            "chat_id": str(chat_id),
            "service": f"Panier ({cart_count(chat_id)} art.)",
            "activity": _cart_lines(chat_id, "fr"),
            "deadline": "",
            "name": data.get("name", ""),
            "phone": "",
            "status": "en attente",
            "created_at": actions._now(),
        })
        clear_cart(chat_id)
        actions._clear_flow(chat_id)
        actions.upsert_client(chat_id, name=data.get("name", ""))
        bot.send_message(
            chat_id,
            tt(lang, "checkout_done", oid=order_id, total=total,
               whatsapp=actions.WHATSAPP_FALLBACK),
        )
        actions.notify_admin(
            bot,
            f"🛒 COMMANDE CATALOGUE\n👤 {data.get('name','')} — chat_id {chat_id}\n"
            f"📍 {data.get('adresse','')}\n💰 Total : {total:g}€",
        )
        return True

    actions._clear_flow(chat_id)
    return False


# ---------------------------------------------------------------------------
# Admin : gestion du catalogue
# ---------------------------------------------------------------------------

def admin_product(bot, chat_id: int, args: str, lang: str = "fr") -> None:
    """/produit add <cat>|<nom>|<desc>|<prix> — del <id> — maj <id>|<prix> — list"""
    parts = args.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub in {"", "list", "ls"}:
        with DB_LOCK:
            rows = DB_CONN.execute(
                "SELECT id, category, name, price, active FROM products ORDER BY id"
            ).fetchall()
        if not rows:
            bot.send_message(chat_id, tt(lang, "prod_usage"))
            return
        items = "\n".join(
            f"{pid}. {cat} — {name} — {price:g}€ {'✅' if act else '❌'}"
            for pid, cat, name, price, act in rows
        )
        bot.send_message(chat_id, tt(lang, "prod_list", items=items))
        return

    if sub == "add":
        fields = [f.strip() for f in rest.split("|")]
        if len(fields) < 4:
            bot.send_message(chat_id, tt(lang, "prod_usage"))
            return
        cat, name, desc, price_s = fields[0], fields[1], fields[2], fields[3]
        try:
            price = float(price_s.replace(",", "."))
        except ValueError:
            bot.send_message(chat_id, tt(lang, "prod_usage"))
            return
        with DB_LOCK:
            cur = DB_CONN.execute(
                "INSERT INTO products (category, name, description, price, active, created_at)"
                " VALUES (?,?,?,?,1,?)",
                (cat, name, desc, price, _now()),
            )
            DB_CONN.commit()
            pid = cur.lastrowid
        bot.send_message(chat_id, tt(lang, "prod_added", pid=pid, name=name, price=price))
        return

    if sub in {"del", "sup"}:
        try:
            pid = int(rest)
        except ValueError:
            bot.send_message(chat_id, tt(lang, "prod_usage"))
            return
        with DB_LOCK:
            DB_CONN.execute("UPDATE products SET active = 0 WHERE id = ?", (pid,))
            DB_CONN.commit()
        bot.send_message(chat_id, tt(lang, "prod_deleted", pid=pid))
        return

    if sub in {"maj", "prix", "update"}:
        fields = [f.strip() for f in rest.split("|")]
        try:
            pid, price = int(fields[0]), float(fields[1].replace(",", "."))
        except (ValueError, IndexError):
            bot.send_message(chat_id, tt(lang, "prod_usage"))
            return
        with DB_LOCK:
            DB_CONN.execute("UPDATE products SET price = ? WHERE id = ?", (price, pid))
            DB_CONN.commit()
        bot.send_message(chat_id, tt(lang, "prod_price_set", pid=pid, price=price))
        return

    bot.send_message(chat_id, tt(lang, "prod_usage"))
