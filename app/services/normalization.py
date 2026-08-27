import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


INVOICE_CALCULATED_FIELDS = {"gesamt_netto", "tva", "gesamtbetrag"}
POSITION_CALCULATED_FIELDS = {"gesamt_netto", "tva", "gesamtpreis"}


def normalize_client_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = text.replace("&", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_invoice_type(value: Any, valid_types: set[str]) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    return text if text in valid_types else "unknown"


def normalize_invoice_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    formats = [
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return _to_decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("-") or text.startswith("−") or (text.startswith("(") and text.endswith(")"))
    text = text.replace("−", "-")
    text = re.sub(r"[^\d,.\-]", "", text)
    text = text.replace("-", "")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) in {1, 2}:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        parts = text.split(".")
        if len(parts[-1]) in {1, 2}:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = "".join(parts)

    if negative:
        text = f"-{text}"
    return _to_decimal(text)


def _to_decimal(value: str) -> Decimal | None:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_extraction(raw: dict[str, Any], valid_invoice_types: set[str]) -> dict[str, Any]:
    client = raw.get("client") or {}
    invoice = raw.get("invoice") or {}
    positions = raw.get("invoice_pos") or []

    normalized = {
        "client": {
            "name_original": _clean_string(client.get("name")),
            "name_normalized": normalize_client_name(client.get("name")),
            "street": _clean_string(client.get("street")),
            "house_number": _clean_string(client.get("house_number")),
            "postal_code": _clean_string(client.get("postal_code")),
            "city": _clean_string(client.get("city")),
        },
        "invoice": {
            "invoice_number": _clean_string(invoice.get("invoice_number")),
            "invoice_date": normalize_invoice_date(invoice.get("invoice_date")),
            "invoice_type": normalize_invoice_type(invoice.get("invoice_type"), valid_invoice_types),
            "gesamt_netto": normalize_amount(invoice.get("gesamt_netto")),
            "tva": normalize_amount(invoice.get("tva")),
            "gesamtbetrag": normalize_amount(invoice.get("gesamtbetrag")),
            "calculated_fields": _normalize_calculated_fields(
                invoice.get("calculated_fields"),
                INVOICE_CALCULATED_FIELDS,
            ),
        },
        "invoice_pos": [
            {
                "pos_number": index,
                "description": _clean_string(item.get("description")),
                "gesamt_netto": normalize_amount(item.get("gesamt_netto")),
                "tva": normalize_amount(item.get("tva")),
                "gesamtpreis": normalize_amount(item.get("gesamtpreis")),
                "calculated_fields": _normalize_calculated_fields(
                    item.get("calculated_fields"),
                    POSITION_CALCULATED_FIELDS,
                ),
            }
            for index, item in enumerate(positions, start=1)
        ],
    }
    _complete_amount_triplet(
        normalized["invoice"],
        net_key="gesamt_netto",
        tax_key="tva",
        gross_key="gesamtbetrag",
    )
    has_position_level_tax = any(position["tva"] is not None for position in normalized["invoice_pos"])
    for position in normalized["invoice_pos"]:
        _complete_amount_triplet(
            position,
            net_key="gesamt_netto",
            tax_key="tva",
            gross_key="gesamtpreis",
        )
    if not has_position_level_tax:
        _complete_positions_from_invoice_tax_rate(normalized["invoice"], normalized["invoice_pos"])
    return normalized


def serialize_normalized(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize_normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_normalized(item) for item in value]
    return value


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _normalize_calculated_fields(value: Any, allowed_fields: set[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    normalized: list[str] = []
    for item in items:
        field = str(item).strip()
        if field in allowed_fields and field not in normalized:
            normalized.append(field)
    return normalized


def _complete_amount_triplet(container: dict[str, Any], *, net_key: str, tax_key: str, gross_key: str) -> None:
    net = container.get(net_key)
    tax = container.get(tax_key)
    gross = container.get(gross_key)
    calculated_fields = set(container.get("calculated_fields") or [])

    if net is None and tax is not None and gross is not None:
        container[net_key] = _to_decimal(str(gross - tax))
        calculated_fields.add(net_key)
    elif tax is None and net is not None and gross is not None:
        container[tax_key] = _to_decimal(str(gross - net))
        calculated_fields.add(tax_key)
    elif gross is None and net is not None and tax is not None:
        container[gross_key] = _to_decimal(str(net + tax))
        calculated_fields.add(gross_key)

    container["calculated_fields"] = [field for field in container.get("calculated_fields", []) if field in calculated_fields] + [
        field for field in (net_key, tax_key, gross_key)
        if field in calculated_fields and field not in container.get("calculated_fields", [])
    ]


def _complete_positions_from_invoice_tax_rate(invoice: dict[str, Any], positions: list[dict[str, Any]]) -> None:
    tax_rate = _invoice_tax_rate(invoice)
    if tax_rate is None:
        return

    for position in positions:
        net = position.get("gesamt_netto")
        tax = position.get("tva")
        gross = position.get("gesamtpreis")
        calculated_fields = set(position.get("calculated_fields") or [])

        if net is not None and tax is None:
            position["tva"] = _to_decimal(str(net * tax_rate))
            calculated_fields.add("tva")
            tax = position["tva"]

        if net is not None and gross is None and tax is not None:
            position["gesamtpreis"] = _to_decimal(str(net + tax))
            calculated_fields.add("gesamtpreis")
            gross = position["gesamtpreis"]

        if gross is not None and net is None:
            divisor = Decimal("1.00") + tax_rate
            if divisor == Decimal("0.00"):
                continue
            position["gesamt_netto"] = _to_decimal(str(gross / divisor))
            calculated_fields.add("gesamt_netto")
            net = position["gesamt_netto"]

        if gross is not None and tax is None and net is not None:
            position["tva"] = _to_decimal(str(gross - net))
            calculated_fields.add("tva")

        position["calculated_fields"] = [
            field for field in position.get("calculated_fields", []) if field in calculated_fields
        ] + [
            field
            for field in ("gesamt_netto", "tva", "gesamtpreis")
            if field in calculated_fields and field not in position.get("calculated_fields", [])
        ]


def _invoice_tax_rate(invoice: dict[str, Any]) -> Decimal | None:
    net = invoice.get("gesamt_netto")
    tax = invoice.get("tva")
    if net is None or tax is None or net == Decimal("0.00"):
        return None
    return tax / net
