def render_consensus_telemetry(bot, symbol, p_final, modo, votos, regime=None):
    """Muestra telemetría del consenso TRINITY (MT/SR/G)."""
    if modo == "NONE" and p_final < 40:
        return

    icon = "🔥 REAL" if modo == "REAL" else "🧪 SHADOW"
    regime_icon = f" | 🌪️ {regime}" if regime == "CHAOS" else (f" | 🌊 {regime}" if regime else "")

    def get_icon(score):
        if score >= 70:
            return "🟢"
        if score <= 30:
            return "🔴"
        return "🟡"

    dna_list = [get_icon(votos.get(key, 50)) for key in ["MT", "SR", "G"]]
    dna_str = f"[ {' '.join(dna_list)} ]"

    message = (
        f"📡 {symbol} {icon}{regime_icon} | Prob: {p_final:.1f}% | DNA: {dna_str}\n"
        f"   👻 IA:{votos.get('G', 50):.0f}% 📈 TEND:{votos.get('MT', 50):.0f}% 🧱 ESTR:{votos.get('SR', 50):.0f}%"
    )
    bot.log(message)
