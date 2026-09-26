"""MADOS: сведение классов, повторы MARIDA, отбор сцен для эксперимента, проверка детектора с порогом сервиса."""
import json

import pytest

from pipeline.config import (DATA, GROUPS, MADOS_CLASSES, MADOS_OIL, MADOS_TO_GROUP, MARIDA_CLASSES, MARIDA_TO_GROUP,
                             MARIDA_TO_MADOS, RAW)
from pipeline.provenance import load_yaml

HAVE_DATA = (RAW / "MADOS.zip").exists() and (RAW / "marida" / "splits").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="MADOS или MARIDA не скачаны в data/raw")


def test_mados_classes_mapped_to_groups():
    assert set(MADOS_TO_GROUP) == set(MADOS_CLASSES)
    assert set(MADOS_TO_GROUP.values()) <= set(range(len(GROUPS)))
    # Положительный класс — только Marine Debris; слизь, медузы и нефть мусором не считаются
    assert [c for c, g in MADOS_TO_GROUP.items() if g == GROUPS.index("debris")] == [1]
    assert MADOS_CLASSES[MADOS_OIL] == "Oil Spill" and MADOS_TO_GROUP[MADOS_OIL] == GROUPS.index("water")


def test_marida_and_mados_classes_agree():
    """Один и тот же класс в MARIDA и MADOS попадает в одну группу детектора."""
    for a, b in MARIDA_TO_MADOS.items():
        assert MARIDA_TO_GROUP[a] == MADOS_TO_GROUP[b], (MARIDA_CLASSES[a], MADOS_CLASSES[b])


@needs_data
def test_marida_scenes_found_inside_mados():
    """Все 63 сцены MARIDA есть в MADOS, и разбиение у них то же (train → train, test → test)."""
    from pipeline import mados

    dup = mados.duplicates()
    assert len(dup) == 63 and len(set(dup.values())) == 63
    marida = {}
    for s in ("train", "val", "test"):
        for p in (RAW / "marida" / "splits" / f"{s}_X.txt").read_text().split():
            marida[p.rsplit("_", 1)[0]] = s
    sp = mados.scene_splits()
    assert all(sp[s] == marida[k] for s, k in dup.items())


@needs_data
def test_mados_training_excludes_test_and_duplicates():
    """В эксперименте с MADOS (train --with-mados) в обучение идут только новые сцены train и val."""
    from pipeline import mados

    used = {mados.scene_of(p) for s in ("train", "val") for p in mados.new_ids(s)}
    sp = mados.scene_splits()
    assert used and all(sp[s] != "test" for s in used), "сцена MADOS test попала бы в обучение"
    assert not used & set(mados.duplicates()), "сцена MARIDA попала бы в обучение второй раз"
    assert len({mados.scene_of(p) for p in mados.new_ids("test")}) == 27


def test_mados_eval_uses_service_threshold():
    m = json.loads((DATA / "eval" / "detector" / "mados.json").read_text(encoding="utf-8"))
    assert m["p_det"] == load_yaml("detector.yaml")["p_det"]
    assert m["n_scenes"] == 27 and m["n_debris_px"] > 0
