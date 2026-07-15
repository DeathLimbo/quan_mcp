from packages.common import (
    ApiResponse,
    DataStaleError,
    ErrorCode,
    UnknownInstrumentError,
    err,
    ok,
)


def test_ok_envelope():
    r = ok({"foo": 1}, trace_id="t", request_id="r")
    assert r["ok"] is True
    assert r["error"] is None
    assert r["data"] == {"foo": 1}
    assert r["trace_id"] == "t"


def test_err_envelope_from_quant_error():
    r = err(DataStaleError("watermark old"), trace_id="t")
    assert r["ok"] is False
    assert r["data"] is None
    assert r["error"]["code"] == ErrorCode.DATA_STALE.value
    assert r["error"]["message"] == "watermark old"


def test_err_envelope_from_generic_exception():
    r = err(RuntimeError("boom"))
    assert r["error"]["code"] == ErrorCode.INTERNAL_ERROR.value


def test_api_response_model_validates():
    m = ApiResponse.model_validate(err(UnknownInstrumentError("bad iid")))
    assert m.ok is False
    assert m.error is not None
    assert m.error.code is ErrorCode.UNKNOWN_INSTRUMENT
