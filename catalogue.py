# ---------------------------------------------------------------------------
# Catalogue + panier 100% locaux — Komara Agency 🇬🇳
# Tables : products, cart. Zéro IA externe, zéro dépendance réseau.
# ---------------------------------------------------------------------------

import re
import sqlite3
import threading
from datetime import datetime, timezone

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

DB_CONN = None
DB_LOCK = threading.Lock()

CATALOGUE_BANNER_URL = (
    "https://media.base44.com/images/public/6a46fe47a5c0862cd5d4cba9/1b47143d3_generated_image.png"
)



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
                [(cat, name, desc, price, now) for cat, name, desc, price in OFFICIAL_SERVICES_2026],
            )
            conn.commit()
        _migrate_official_prices_2026(conn)
        _migrate_official_prices_2027_bots(conn)


# Catalogue officiel — Komara conçoit des BOTS et AGENTS IA 🇬🇳
# Mêmes tarifs que la grille officielle de l'agence. Agent IA et Bot ont un
# tarif récurrent (maintenance) précisé dans la description : le champ price
# reste le montant d'appel (setup / unité).
OFFICIAL_SERVICES_2026 = [
    ("Bot", "Bot Scripté", "Bot WhatsApp/Telegram : FAQ, menu, commandes simples — prêt en 48h", 50.0),
    ("Chatbot", "Chatbot IA Vendeur", "Chatbot IA qui vend 24/7 : catalogue, panier et devis intégrés", 100.0),
    ("Agent IA", "Agent IA Premium", "Agent IA multicanal : mémoire, relances, prise de commande (+50€/mois maintenance)", 150.0),
    ("Maintenance", "Maintenance mensuelle", "Agent IA : 50€/mois — Bot : 20€/mois. Mises à jour et corrections incluses", 50.0),
]


def _migrate_official_prices_2026(conn: sqlite3.Connection) -> None:
    """Migration one-shot : remplace l'ancien catalogue par défaut par les
    prix officiels 2026, SANS toucher aux produits déjà personnalisés par
    l'admin (/produit add|maj) ni aux paniers/commandes en cours — les
    anciens produits sont juste désactivés (les id restent valides pour les
    commandes déjà passées)."""
    done = conn.execute(
        "SELECT value FROM bot_state WHERE key = 'catalogue_v2026_migrated'"
    ).fetchone()
    if done:
        return
    old_default_names = {
        "Logo professionnel", "Logo + charte graphique", "Site vitrine",
        "Site e-commerce", "Agent WhatsApp", "Agent multi-canal",
        "Pack visuels (10)", "Vidéo animée IA", "Formation IA (2h)",
    }
    rows = conn.execute("SELECT id, name FROM products WHERE active = 1").fetchall()
    untouched = all(name in old_default_names for _pid, name in rows) if rows else True
    if untouched:
        conn.execute("UPDATE products SET active = 0 WHERE name IN ({})".format(
            ",".join("?" for _ in old_default_names)), tuple(old_default_names))
        now = _now()
        conn.executemany(
            "INSERT INTO products (category, name, description, price, active, created_at)"
            " VALUES (?,?,?,?,1,?)",
            [(cat, name, desc, price, now) for cat, name, desc, price in OFFICIAL_SERVICES_2026],
        )
    conn.execute(
        "INSERT OR REPLACE INTO bot_state (key, value) VALUES ('catalogue_v2026_migrated', '1')"
    )


def _migrate_official_prices_2027_bots(conn: sqlite3.Connection) -> None:
    """Migration one-shot : passage au catalogue 100% BOTS & AGENTS IA.
    Désactive les anciens produits 2026 (logo, visuel, site...), insère les
    nouvelles offres (Bot, Chatbot, Agent IA, Maintenance). Ne touche pas
    aux produits personnalisés par l'admin ni aux commandes en cours — les
    id des anciens produits restent valides pour l'historique."""
    done = conn.execute(
        "SELECT value FROM bot_state WHERE key = 'catalogue_v2027_bots_migrated'"
    ).fetchone()
    if done:
        return
    old_2026_names = {
        "Logo Pro", "Visuel / Affiche", "Site Vitrine", "Site E-commerce",
        "Chatbot IA", "Vidéo IA", "Formation IA",
        "Logo professionnel", "Logo + charte graphique", "Site vitrine",
        "Site e-commerce", "Agent WhatsApp", "Agent multi-canal",
        "Pack visuels (10)", "Vidéo animée IA", "Formation IA (2h)",
    }
    # NB : appelée depuis init_catalogue_db déjà sous DB_LOCK — ne pas re-verrouiller.
    new_names = {name for _cat, name, *_ in OFFICIAL_SERVICES_2026}
    conn.execute("UPDATE products SET active = 0 WHERE name IN ({})".format(
        ",".join("?" for _ in old_2026_names)), tuple(old_2026_names))
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM products WHERE name IN ({})".format(
            ",".join("?" for _ in new_names)), tuple(new_names)).fetchall()}
    now = _now()
    conn.executemany(
        "INSERT INTO products (category, name, description, price, active, created_at)"
        " VALUES (?,?,?,?,1,?)",
        [(cat, name, desc, price, now) for cat, name, desc, price in OFFICIAL_SERVICES_2026
         if name not in existing],
    )
    conn.execute(
        "INSERT OR REPLACE INTO bot_state (key, value)"
        " VALUES ('catalogue_v2027_bots_migrated', '1')"
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Textes multilingues
# ---------------------------------------------------------------------------

TT: dict[str, dict[str, str]] = {
    "fr": {
        "catalogue_caption": '💎 KOMARA AGENCY 🇬🇳 — CATALOGUE OFFICIEL 2026\nUn clic pour choisir 👇',
        "btn_choose": '🛒 {name} — {price:g}€',
        "btn_order": '🚀 Commander',
        "btn_quote": '📄 Devis gratuit',
        "btn_finalize": '✅ Finaliser ma commande',
        "chosen": '✅ Tu as choisi : {name}\n💰 {price:g}€\n🛒 Ajouté au panier ({count} article(s) — {total:g}€)\n\nContinue de choisir, ou finalise ta commande 👇',
        "cta_after_choice": "Envie d'un autre service, ou on finalise ?",
        "order_from_empty": "🛒 Ton panier est vide pour l'instant.\nChoisis un service dans le catalogue ci-dessus 👆",
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
        "catalogue_caption": '💎 KOMARA AGENCY 🇬🇳 — OFFICIAL 2026 CATALOGUE\nOne tap to choose 👇',
        "btn_choose": '🛒 {name} — {price:g}€',
        "btn_order": '🚀 Order now',
        "btn_quote": '📄 Free quote',
        "btn_finalize": '✅ Finalize my order',
        "chosen": '✅ You picked: {name}\n💰 {price:g}€\n🛒 Added to cart ({count} item(s) — {total:g}€)\n\nKeep choosing, or finalize your order 👇',
        "cta_after_choice": 'Want another service, or shall we finalize?',
        "order_from_empty": '🛒 Your cart is empty for now.\nPick a service from the catalogue above 👆',
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
        "catalogue_caption": '💎 KOMARA AGENCY 🇬🇳 — CATÁLOGO OFICIAL 2026\nUn clic para elegir 👇',
        "btn_choose": '🛒 {name} — {price:g}€',
        "btn_order": '🚀 Pedir ahora',
        "btn_quote": '📄 Presupuesto gratis',
        "btn_finalize": '✅ Finalizar mi pedido',
        "chosen": '✅ Elegiste: {name}\n💰 {price:g}€\n🛒 Añadido al carrito ({count} artículo(s) — {total:g}€)\n\nSigue eligiendo o finaliza su pedido 👇',
        "cta_after_choice": '¿Otro servicio, o finalizamos?',
        "order_from_empty": '🛒 Su carrito está vacío por ahora.\nElige un servicio del catálogo arriba 👆',
        "catalogue_head": "🛍️ Catálogo Komara Agency 🇬🇳\n\n{items}\n\n➕ Añadir: escriba 'añadir <número>'\n🛒 Ver carrito: 'carrito'",
        "catalogue_item": "{n}. {name} — {price:g}€\n   {desc}",
        "catalogue_empty": "🛍️ El catálogo llega muy pronto. Mientras tanto, escriba 'presupuesto' para un proyecto a medida 👇",
        "added": "✅ {name} añadido (x{qty})\n🛒 Carrito: {count} artículo(s) — {total:g}€\nEscribe 'carrito' para ver, 'ordenar' para finalizar 🚀",
        "not_found": "No encontré ese número. Escriba 'catálogo' para ver la lista 🙏",
        "cart_head": "🛒 Su carrito:\n\n{items}\n\n➡️ Total: {total:g}€\n\n'ordenar' para finalizar • 'vaciar' para cancelar",
        "cart_line": "• {name} x{qty} — {line:g}€",
        "cart_empty": "🛒 Su carrito está vacío. Escriba 'catálogo' para ver las ofertas 👀",
        "cleared": "🛒 Carrito vaciado 🙏",
        "checkout_start": "🚀 Finalizamos su pedido:\n\n{items}\n\n➡️ Total: {total:g}€\n\n📍 ¿Entrega? (ciudad, zona o enlace de recogida)",
        "checkout_known_name": "📍 ¿Es para {name}? (ok para confirmar o escriba el nombre)",
        "checkout_name": "👤 ¿A qué nombre?",
        "checkout_confirm": "✅ Resumen:\n\n{items}\n➡️ Total: {total:g}€\n📍 Entrega: {adresse}\n\nEscribe 'confirmar' para validar, 'cancelar' para parar.",
        "checkout_done": "🎉 Pedido n°{oid} registrado.\n\nTotal: {total:g}€\nEl equipo le contacta en 24h para el pago y la entrega.\nGracias por su confianza 🇬🇳\n¿Una pregunta? WhatsApp {whatsapp}",
        "prod_added": "✅ Producto añadido (id {pid}): {name} — {price:g}€",
        "prod_deleted": "🗑️ Producto {pid} eliminado",
        "prod_price_set": "✅ Producto {pid}: nuevo precio {price:g}€",
        "prod_list": "🛍️ Catálogo (admin):\n\n{items}",
        "prod_usage": "Uso: /produit add <categoría>|<nombre>|<descripción>|<precio>\n/produit del <id> • /produit maj <id>|<precio> • /produits",
    },
    "ar": {
        "catalogue_caption": '💎 كومارا أجنسِي 🇬🇳 — الكتالوج الرسمي 2026\nنقرة واحدة للاختيار 👇',
        "btn_choose": '🛒 {name} — {price:g}€',
        "btn_order": '🚀 اطلب الآن',
        "btn_quote": '📄 عرض سعر مجاني',
        "btn_finalize": '✅ إنهاء طلبي',
        "chosen": '✅ اخترت: {name}\n💰 {price:g}€\n🛒 أضيف إلى السلة ({count} عنصر — {total:g}€)\n\nتابع الاختيار أو أنهِ طلبك 👇',
        "cta_after_choice": 'خدمة أخرى، أم ننهي؟',
        "order_from_empty": '🛒 سلتك فارغة الآن.\nاختر خدمة من الكتالوج أعلاه 👆',
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
            " WHERE active = 1 ORDER BY id"
        ).fetchall()
        promo = 0.0
        try:
            _p = DB_CONN.execute(
                "SELECT pct FROM global_promo WHERE id = 1").fetchone()
            if _p:
                promo = float(_p[0])
        except sqlite3.OperationalError:
            pass
    # Lot 20 : promo globale (via /solde, /promo, /KA, /bonnus) appliquée
    # à TOUT le catalogue — les paniers et totaux suivent automatiquement.
    if promo > 0:
        rows = [(pid, name, desc, round(price * (1 - promo / 100), 2))
                for pid, name, desc, price in rows]
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
    try:
        import cart_nudge
        cart_nudge._clear(str(chat_id))
    except Exception:
        pass


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
    import cart_nudge
    if cart_count(chat_id):
        cart_nudge.mark_seen(chat_id, lang, cart_total(chat_id))
    if cart_count(chat_id) == 0:
        bot.send_message(chat_id, tt(lang, "cart_empty"))
        return
    bot.send_message(
        chat_id,
        tt(lang, "cart_head", items=_cart_lines(chat_id, lang),
           total=cart_total(chat_id)),
    )


def show_catalogue_inline(bot, chat_id: int, lang: str) -> None:
    """Catalogue avec boutons cliquables (1 par service, boucle sur N produits)."""
    products = active_products()
    if not products:
        bot.send_message(chat_id, tt(lang, "catalogue_empty"))
        return

    rows = [
        [InlineKeyboardButton(
            tt(lang, "btn_choose", name=name, price=price),
            callback_data=f"kmr_add_{pid}",
        )]
        for pid, name, _desc, price in products
    ]
    rows.append([
        InlineKeyboardButton(tt(lang, "btn_order"), callback_data="kmr_order"),
        InlineKeyboardButton(tt(lang, "btn_quote"), callback_data="kmr_quote"),
    ])
    keyboard = InlineKeyboardMarkup(rows)

    caption = tt(lang, "catalogue_caption")
    try:
        bot.send_photo(chat_id, CATALOGUE_BANNER_URL, caption=caption, reply_markup=keyboard)
    except Exception:
        bot.send_message(chat_id, caption, reply_markup=keyboard)


def handle_callback(bot, call, lang: str) -> None:
    """Traite un clic sur un bouton du catalogue inline (kmr_add_<id>, kmr_order, kmr_quote)."""
    import actions
    chat_id = call.message.chat.id
    data = call.data or ""

    if data.startswith("kmr_add_"):
        try:
            pid = int(data.removeprefix("kmr_add_"))
        except ValueError:
            bot.answer_callback_query(call.id)
            return
        with DB_LOCK:
            row = DB_CONN.execute(
                "SELECT name, price FROM products WHERE id = ? AND active = 1", (pid,)
            ).fetchone()
        if not row:
            bot.answer_callback_query(call.id, tt(lang, "not_found"), show_alert=True)
            return
        name, price = row
        with DB_LOCK:
            DB_CONN.execute(
                "INSERT INTO cart (chat_id, product_id, qty, added_at) VALUES (?,?,1,?)"
                " ON CONFLICT(chat_id, product_id) DO UPDATE SET qty = qty + 1",
                (str(chat_id), pid, _now()),
            )
            DB_CONN.commit()
        bot.answer_callback_query(call.id, f"✅ {name}")
        finalize_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(tt(lang, "btn_finalize"), callback_data="kmr_order")
        ]])
        bot.send_message(
            chat_id,
            tt(lang, "chosen", name=name, price=price,
               count=cart_count(chat_id), total=cart_total(chat_id)),
            reply_markup=finalize_kb,
        )
        return

    if data == "kmr_order":
        bot.answer_callback_query(call.id)
        if not start_checkout(bot, chat_id, lang):
            bot.send_message(chat_id, tt(lang, "order_from_empty"))
        return

    if data == "kmr_quote":
        bot.answer_callback_query(call.id)
        actions.start_flow(bot, chat_id, "devis", lang)
        return

    bot.answer_callback_query(call.id)


def handle_client(bot, chat_id: int, text: str, lang: str) -> bool:
    """Route les commandes catalogue/panier. True si le message est consommé."""
    low = text.strip().lower()
    if low in CATALOGUE_TRIGGERS:
        show_catalogue_inline(bot, chat_id, lang)
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
    import cart_nudge
    cart_nudge.mark_seen(chat_id, lang, cart_total(chat_id))  # relance 10 min
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
