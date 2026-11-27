from app.flatten import ROW_KEY, flatten, flatten_text


def test_forecast_points_become_rows_with_context():
    body = {"ForecastResponse": {"PlantName": "bdws", "ForecastCategory": "DayAhead", "ForecastData": [
        {"StartTime": "2026-11-28T00:00:00", "ForecastValue": "0.0"},
        {"StartTime": "2026-11-28T00:15:00", "ForecastValue": "1.5"}]}}
    path, rows = flatten(body)
    assert path == "ForecastResponse.ForecastData"
    assert rows == [
        {"ForecastResponse.PlantName": "bdws", "ForecastResponse.ForecastCategory": "DayAhead", ROW_KEY: 1,
         "StartTime": "2026-11-28T00:00:00", "ForecastValue": "0.0"},
        {"ForecastResponse.PlantName": "bdws", "ForecastResponse.ForecastCategory": "DayAhead", ROW_KEY: 2,
         "StartTime": "2026-11-28T00:15:00", "ForecastValue": "1.5"}]


def test_largest_record_list_wins_and_nested_values_flatten():
    body = {"meta": [{"a": 1}], "data": [{"x": {"y": 1}, "tags": [1, 2]}, {"x": {"y": 2}, "tags": []}]}
    path, rows = flatten(body)
    assert path == "data"
    assert rows[0]["x.y"] == 1 and rows[0]["tags"] == "[1, 2]" and rows[0]["meta[0].a"] == 1


def test_no_records_and_non_json():
    assert flatten({"a": {"b": 1}}) == (None, [{"a.b": 1}])
    assert flatten_text("not json")[1] == [{"not_json": "not json"}]
    assert flatten_text(None) == (None, [])
