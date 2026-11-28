"""Which schedule governs a plant/API when several match: the most specific one."""
from datetime import date

from app.models import PollSchedule
from app.scheduler import effective_schedules, in_force

TODAY = date(2026, 11, 28)


def sch(id, plants=None, cats=None, enabled=True, valid_from=None, valid_to=None):
    return PollSchedule(id=id, name=f"s{id}", plant_ids=plants, categories=cats, kind="interval", interval_minutes=15,
                        enabled=enabled, valid_from=valid_from, valid_to=valid_to)


def gov(schedules, pid, cat):
    s = effective_schedules(schedules, [(pid, cat)], TODAY).get((pid, cat))
    return s.id if s else None


def test_defaults_cover_every_plant():
    s = [sch(1, cats=["mmf"]), sch(2, cats=["rolling"])]
    assert gov(s, 7, "mmf") == 1 and gov(s, 7, "rolling") == 2 and gov(s, 7, "dayahead") is None


def test_chosen_plants_beat_all_plants_even_with_all_apis():
    s = [sch(1, cats=["dayahead"]), sch(2, plants=[7, 8])]
    assert gov(s, 7, "dayahead") == 2
    assert gov(s, 9, "dayahead") == 1


def test_fewer_plants_win_then_fewer_apis():
    s = [sch(1, plants=[7, 8, 9]), sch(2, plants=[7]), sch(3, plants=[7], cats=["mmf", "rolling"]),
         sch(4, plants=[7], cats=["mmf"])]
    assert gov(s, 7, "mmf") == 4
    assert gov(s, 7, "rolling") == 3
    assert gov(s, 7, "dayahead") == 2
    assert gov(s, 8, "mmf") == 1


def test_off_or_out_of_dates_falls_back():
    s = [sch(1, cats=["mmf"]), sch(2, plants=[7], cats=["mmf"], enabled=False),
         sch(3, plants=[8], cats=["mmf"], valid_from=date(2026, 12, 1))]
    assert gov(s, 7, "mmf") == 1
    assert gov(s, 8, "mmf") == 1
    assert [x.id for x in in_force(s, date(2026, 12, 2))] == [3, 1]


def test_dated_beats_open_ended_then_newest_wins():
    s = [sch(1, plants=[7], cats=["mmf"]), sch(2, plants=[7], cats=["mmf"], valid_to=date(2026, 12, 31))]
    assert gov(s, 7, "mmf") == 2
    s = [sch(1, plants=[7, 8], cats=["mmf"]), sch(5, plants=[7, 9], cats=["mmf"])]
    assert gov(s, 7, "mmf") == 5


def test_plant_removed_from_list_governs_nothing():
    s = [sch(1), sch(2, plants=[])]
    assert gov(s, 7, "mmf") == 1
