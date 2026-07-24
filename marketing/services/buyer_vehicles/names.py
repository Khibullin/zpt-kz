from __future__ import annotations


def suggest_audience_name(vehicle_selection: list[dict]) -> str:
    if not vehicle_selection:
        return 'Покупатели по заявкам на запчасти'

    parts: list[str] = []
    for entry in vehicle_selection:
        brand = entry.get('brand') or ''
        if entry.get('all_models') or not entry.get('models'):
            parts.append(brand)
            continue
        models = entry.get('models') or []
        if len(models) == 1:
            parts.append(f'{brand} {models[0]}')
        else:
            joined = '/'.join(models)
            parts.append(f'{brand} {joined}')

    if len(parts) == 1 and ' ' not in parts[0]:
        return f'{parts[0]} — все модели — покупатели по заявкам'
    if len(parts) == 1:
        return f'{parts[0]} — покупатели по заявкам'
    if len(parts) <= 3:
        return ' + '.join(parts) + ' — покупатели'
    return f'{parts[0]} + ещё {len(parts) - 1} — покупатели по заявкам'
