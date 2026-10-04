from statistics import mean


def entry_metrics(entry, nominal_mah):
    result = {}
    discharged = entry.get("discharged_mah")
    if entry.get("kind") == "test" and discharged is not None:
        result["measured_mah"] = discharged
        result["measured_ratio"] = round(100 * discharged / nominal_mah, 1)
        if discharged > nominal_mah:
            result["above_nominal_caution"] = (
                "Capacité mesurée supérieure au nominal : dépassement constaté. "
                "Vérifiez les conditions du test (courant, coupure, température) "
                "et la capacité nominale déclarée ; la mesure n'est pas automatiquement invalide."
            )
    initial = entry.get("initial_percent")
    final = entry.get("final_percent")
    added = entry.get("added_mah")
    if entry.get("kind") == "charge" and initial is not None and final is not None and added is not None:
        span = (final - initial) / 100
        if span >= 0.2 and added > 0:
            result["estimated_mah"] = round(added / span)
            result["estimate_caution"] = "Estimation approximative, surtout si le pourcentage provient de la tension."
            if result["estimated_mah"] > nominal_mah:
                result["above_nominal_caution"] = (
                    "Estimation supérieure au nominal : dépassement constaté. "
                    "Vérifiez l'estimation du pourcentage initial, le pourcentage final et les conditions "
                    "de charge ; l'estimation n'est pas automatiquement invalide."
                )
        elif span > 0:
            result["estimate_caution"] = "Variation de charge trop faible (< 20 points) pour une estimation utile."
    for field, label in (("before_v", "before_delta_v"), ("after_v", "after_delta_v"), ("resistance_mohm", "resistance_delta_mohm")):
        values = [value for value in (entry.get(field) or []) if value is not None]
        if len(values) >= 2:
            result[label] = round(max(values) - min(values), 3)
            if field == "resistance_mohm":
                result["resistance_mean_mohm"] = round(mean(values), 2)
        elif field == "resistance_mohm" and values:
            result["resistance_mean_mohm"] = round(values[0], 2)
    return result
