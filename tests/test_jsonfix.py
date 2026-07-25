"""Тесты починки кривого JSON — WB иногда отвечает 200 с невалидным телом."""

from __future__ import annotations

import json

import pytest

from mp_parser.jsonfix import JsonRepairError, fragment_around, loads_lenient, repair


def test_valid_json_untouched():
    payload = {"data": {"products": [{"id": 1, "name": "Кофе"}]}}
    assert loads_lenient(json.dumps(payload, ensure_ascii=False)) == payload


@pytest.mark.parametrize(
    "broken,expected",
    [
        ('{"a": 1,}', {"a": 1}),                      # висячая запятая в объекте
        ('{"a": [1, 2,]}', {"a": [1, 2]}),            # висячая запятая в массиве
        ('{"a": 1,,"b": 2}', {"a": 1, "b": 2}),       # пропущенная пара
        ('{"a": [1,,2]}', {"a": [1, 2]}),             # пропущенный элемент
        ("{'a': 1}", {"a": 1}),                        # ключ в одинарных кавычках
        ('{"a": \'кофе\'}', {"a": "кофе"}),           # значение в одинарных кавычках
        ('{"a": "к\x01офе"}', {"a": "кофе"}),         # управляющий символ в строке
    ],
)
def test_repairs_common_breakage(broken, expected):
    assert loads_lenient(broken, dump_on_failure=False) == expected


def test_trailing_garbage_is_cut():
    assert loads_lenient('{"a": 1} мусор в хвосте', dump_on_failure=False) == {"a": 1}


def test_repair_keeps_valid_content_intact():
    """Починка не должна портить запятые и кавычки внутри строк."""
    text = '{"name": "Кофе, 1 кг", "note": "цена, скидка"}'
    assert json.loads(repair(text)) == json.loads(text)


def test_partial_recovery_keeps_whole_products():
    """Товары, пришедшие до поломки, важнее самой поломки."""
    broken = '{"products": [' + '{"id": 1},' * 20 + '{"id": ¿}]}'
    payload = loads_lenient(broken, dump_on_failure=False)
    assert len(payload["products"]) == 20


def test_unfixable_raises_with_fragment():
    with pytest.raises(JsonRepairError) as info:
        loads_lenient("это вообще не json", url="https://search.wb.ru/x", dump_on_failure=False)

    error = info.value
    assert "это вообще не json" in error.fragment  # видно само проблемное место
    assert "^" in error.fragment  # и указатель на позицию
    assert error.dump is None


def test_dump_is_written(tmp_path, monkeypatch):
    import mp_parser.jsonfix as jsonfix

    monkeypatch.setattr(jsonfix, "DUMP_DIR", tmp_path / "dumps")
    with pytest.raises(JsonRepairError) as info:
        loads_lenient('{"a": ¿}', url="https://search.wb.ru/exactmatch")

    dump = info.value.dump
    assert dump is not None and dump.exists()
    assert dump.read_text(encoding="utf-8") == '{"a": ¿}'
    assert "search.wb.ru" in dump.name


def test_fragment_marks_position():
    text = '{"a": 1, "b": ?}'
    fragment = fragment_around(text, 14, width=5)
    lines = fragment.splitlines()
    assert lines[1].endswith("^")
    # указатель стоит ровно под проблемным символом
    assert lines[0][lines[1].index("^")] == "?"


# --- реальная поломка WB: валидный JSON + приклеенный обрывок чужого ответа --- #

WB_REAL_BREAKAGE = (
    '{"metadata":{"name":"кофе","catalog_type":"presets",'
    '"preset_normquery_map":{"500050269":"кофе растворимый"}},ot Found'
)


def test_salvage_recovers_head_of_wb_response():
    """Именно этот случай пришёл с живого WB: «…}},ot Found»."""
    payload = loads_lenient(WB_REAL_BREAKAGE, dump_on_failure=False)
    assert payload["metadata"]["name"] == "кофе"


def test_salvage_keeps_products_before_breakage():
    """Если товары успели прийти до мусора — они должны сохраниться."""
    broken = (
        '{"data":{"products":['
        '{"id":1,"name":"Кофе A"},'
        '{"id":2,"name":"Кофе B"}'
        ']},"extra":<<мусор>>'
    )
    payload = loads_lenient(broken, dump_on_failure=False)
    products = payload["data"]["products"]
    assert [p["id"] for p in products] == [1, 2]


def test_salvage_returns_none_for_valid_json():
    from mp_parser.jsonfix import salvage

    assert salvage('{"a": 1}') is None


def test_salvage_gives_up_on_hopeless_input():
    from mp_parser.jsonfix import salvage

    assert salvage("совсем не json") is None


def test_bracket_stack_tracks_strings():
    from mp_parser.jsonfix import _bracket_stack

    # Скобки внутри строк не считаются
    assert _bracket_stack('{"a": "}{[", "b": [1') == ["}", "]"]  # в порядке открытия
    assert _bracket_stack('{"a": "не закрыта') is None  # оборвано внутри строки
    assert _bracket_stack('{"a": 1}]') is None  # лишняя закрывающая
