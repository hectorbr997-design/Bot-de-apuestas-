import os
import asyncio
import logging
from datetime import datetime, time
from anthropic import Anthropic
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CHAT_ID = os.environ["CHAT_ID"]          # Tu chat ID personal
TIMEZONE = os.environ.get("TIMEZONE", "America/Bogota")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Prompts del sistema ────────────────────────────────────────────────

SYSTEM_FOOTBALL = """Eres El Jeque, experto en predicciones de fútbol. Usas tres metodologías:

1. MODELO XG: Distancia euclidiana + decaimiento exponencial (explica 93% varianza).
2. TÉCNICA POISSON: 6 parámetros de XG promedio local/visitante/liga.
3. MODELO BAYESIANO DIXON-COLES: Poder ofensivo/defensivo como distribuciones, ventaja local ~0.35, validado con RPS ~0.196.

Criterio de uso: funciona mejor cuando AMBOS equipos buscan ganar.
Gestión: Criterio de Kelly para tamaño de apuesta.

Cuando analices un partido responde con este formato Telegram (usa emojis y markdown):

⚽ *LOCAL vs VISITANTE*
📊 *Liga:* Premier League

*PROBABILIDADES*
🏠 Local: XX% → cuota justa X.XX
🤝 Empate: XX% → cuota justa X.XX
✈️ Visitante: XX% → cuota justa X.XX

*XG ESTIMADO*
⚡ Local: X.X | Visitante: X.X

*ANÁLISIS XG + POISSON*
[análisis en 2-3 frases]

*MODELO BAYESIANO*
[análisis en 2-3 frases]

*💰 VALOR DETECTADO*
[Si hay valor: explica qué apuesta tiene valor y por qué]
[Si no hay valor: indica "Sin valor claro en este partido"]

*📏 KELLY: X.X% del bankroll*

*🎯 PICK: [LA APUESTA CONCRETA O "NO JUGAR"]*

⚠️ _Análisis estadístico. No garantiza resultados._"""

SYSTEM_NBA = """Eres El Jeque, experto en NBA player props. Metodología de Exclusive Analytics:

1. MATCHUP: Qué tan favorable/desfavorable es el rival en esa categoría.
2. H2H: Historial del jugador vs ese equipo.
3. FORMA RECIENTE: Últimos 4-5 partidos.
4. GAME SCRIPT: Spread grande → blowout → menos minutos → favorece UNDER.
5. MINUTOS PROYECTADOS: Afectan todos los props.
6. KELLY: Gestión de riesgo.

Cuando analices un prop responde con este formato Telegram:

🏀 *JUGADOR — O/U X.X CATEGORÍA*
🆚 *Rival:* Equipo

*PROBABILIDADES*
📈 OVER: XX% → cuota justa X.XX
📉 UNDER: XX% → cuota justa X.XX

*🎯 MATCHUP ANALYSIS*
[análisis en 2-3 frases]

*📊 FORMA RECIENTE*
[análisis en 2-3 frases]

*🎮 GAME SCRIPT*
[análisis en 2-3 frases]

*💰 VALOR*
[Si hay valor: explicación]
[Si no: "Sin valor claro"]

*📏 KELLY: X.X% del bankroll*

*🎯 PICK: [OVER/UNDER X.X o "NO JUGAR"]*

⚠️ _Análisis estadístico. No garantiza resultados._"""

SYSTEM_DAILY = """Eres El Jeque. Cada día generas un resumen de picks para fútbol y NBA.
Basándote en tu conocimiento de las ligas top (Premier, La Liga, Bundesliga, Serie A, NBA),
identifica 2-3 partidos/props del día con mejor valor esperado.

Responde con este formato Telegram:

🔥 *PICKS DEL DÍA — [FECHA]*

━━━━━━━━━━━━━━━
⚽ *FÚTBOL*
━━━━━━━━━━━━━━━

[Para cada partido con valor:]
• *Local vs Visitante* (Liga)
  Pick: [apuesta] @ [cuota estimada]
  Confianza: 🟢 ALTA / 🟡 MEDIA
  Kelly: X.X% bankroll

━━━━━━━━━━━━━━━
🏀 *NBA PROPS*
━━━━━━━━━━━━━━━

[Para cada prop con valor:]
• *Jugador* vs Rival
  Pick: OVER/UNDER X.X [categoría]
  Confianza: 🟢 ALTA / 🟡 MEDIA
  Kelly: X.X% bankroll

━━━━━━━━━━━━━━━
💡 *PARLAY SUGERIDO*
[Combina 2 picks de confianza alta]

⚠️ _Picks basados en modelos estadísticos (XG, Poisson, Bayesiano, Matchup NBA). No garantizan resultados. Juega con responsabilidad._"""

# ─── Función central: llamar a Claude ───────────────────────────────────

def ask_claude(system: str, message: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": message}]
    )
    return response.content[0].text

# ─── Comandos del bot ────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Bienvenido al Bot del Jeque* ⚡\n\n"
        "Predicciones con XG Model, Poisson, Bayesiano y NBA Props.\n\n"
        "*Comandos disponibles:*\n"
        "⚽ /futbol — Analizar partido de fútbol\n"
        "🏀 /nba — Analizar player prop NBA\n"
        "🔥 /picks — Picks del día automáticos\n"
        "❓ /ayuda — Cómo usar el bot\n\n"
        "O simplemente *escríbeme* lo que quieres analizar.\n\n"
        "_Ej: 'Analiza Arsenal vs Chelsea Premier League'_\n"
        "_Ej: 'Lebron James over 25.5 puntos vs Warriors'_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *Cómo usar El Jeque Bot*\n\n"
        "*⚽ Para fútbol escribe:*\n"
        "`/futbol Arsenal vs Man City Premier League`\n"
        "Opcional: agrega cuotas y contexto\n"
        "`/futbol Real Madrid vs Barcelona La Liga. Cuotas: 2.10 / 3.40 / 3.20. Vinicius lesionado`\n\n"
        "*🏀 Para NBA escribe:*\n"
        "`/nba LeBron James over 25.5 puntos vs Warriors`\n"
        "Opcional: agrega cuotas y contexto\n"
        "`/nba Curry under 4.5 rebotes vs Lakers. Cuota under 1.85. Partido cerrado spread -3`\n\n"
        "*🔥 Picks automáticos:*\n"
        "`/picks` — Te da los mejores picks del día\n\n"
        "*💬 Mensaje libre:*\n"
        "También puedes escribir directamente sin comandos y el bot detecta el deporte automáticamente."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_futbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text(
            "⚽ Dime qué partido analizar:\n`/futbol Arsenal vs Chelsea Premier League`",
            parse_mode="Markdown"
        )
        return
    await process_football(update, query)

async def cmd_nba(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text(
            "🏀 Dime qué prop analizar:\n`/nba LeBron over 25.5 puntos vs Warriors`",
            parse_mode="Markdown"
        )
        return
    await process_nba(update, query)

async def cmd_picks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_daily_picks(update.message.reply_text)

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()

    football_keywords = ["vs", "liga", "premier", "laliga", "bundesliga", "serie a",
                         "futbol", "fútbol", "partido", "local", "visitante", "champions"]
    nba_keywords = ["nba", "over", "under", "puntos", "rebotes", "asistencias",
                    "prop", "player", "lakers", "warriors", "celtics", "heat"]

    football_score = sum(1 for k in football_keywords if k in text)
    nba_score = sum(1 for k in nba_keywords if k in text)

    if nba_score > football_score:
        await process_nba(update, update.message.text)
    else:
        await process_football(update, update.message.text)

# ─── Procesadores ────────────────────────────────────────────────────────

async def process_football(update: Update, query: str):
    msg = await update.message.reply_text("⏳ Analizando con XG + Poisson + Bayesiano...")
    try:
        result = ask_claude(SYSTEM_FOOTBALL, f"Analiza este partido: {query}")
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def process_nba(update: Update, query: str):
    msg = await update.message.reply_text("⏳ Analizando matchup, forma reciente y game script...")
    try:
        result = ask_claude(SYSTEM_NBA, f"Analiza este prop NBA: {query}")
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def send_daily_picks(reply_func):
    msg_obj = await reply_func("⏳ Generando picks del día...")
    try:
        today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%A %d de %B, %Y")
        result = ask_claude(
            SYSTEM_DAILY,
            f"Genera los mejores picks para hoy: {today}. "
            "Identifica partidos y props con mayor valor esperado en las ligas top europeas y NBA."
        )
        await msg_obj.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg_obj.edit_text(f"❌ Error generando picks: {str(e)}")

# ─── Picks diarios automáticos ────────────────────────────────────────────

async def scheduled_daily_picks(app: Application):
    try:
        today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%A %d de %B, %Y")
        result = ask_claude(
            SYSTEM_DAILY,
            f"Genera los mejores picks para hoy: {today}. "
            "Identifica partidos y props con mayor valor esperado en las ligas top europeas y NBA."
        )
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=result,
            parse_mode="Markdown"
        )
        logger.info("Picks diarios enviados correctamente.")
    except Exception as e:
        logger.error(f"Error en picks diarios: {e}")
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Error generando picks automáticos: {str(e)}"
        )

# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("futbol", cmd_futbol))
    app.add_handler(CommandHandler("nba", cmd_nba))
    app.add_handler(CommandHandler("picks", cmd_picks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler: picks diarios a las 9am hora local
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        scheduled_daily_picks,
        trigger="cron",
        hour=9,
        minute=0,
        args=[app]
    )
    scheduler.start()

    logger.info("🤖 El Jeque Bot arrancando...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
