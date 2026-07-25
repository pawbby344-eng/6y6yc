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


def test_unfixable_raises_with_fragment():
    broken = '{"products": [' + '{"id": 1},' * 20 + '{"id": ¿}]}'
    with pytest.raises(JsonRepairError) as info:
        loads_lenient(broken, url="https://search.wb.ru/x", dump_on_failure=False)

    error = info.value
    assert "¿" in error.fragment  # в тексте ошибки видно само проблемное место
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
