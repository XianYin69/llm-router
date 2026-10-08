"""§3 drift guard: the shared codec must stay byte-identical across the two repos.

Fails loudly if either dsm.py is hand-edited instead of regenerated, or if the
generator's source list no longer covers a shared node.
"""
import ast, io, os, re

CLIENT = os.environ.get("SMS_CORE_DSM") or os.path.join(
    os.path.expanduser("~"), ".kilocode", "skills", "skill_manage_system",
    "skill", "scripts", "dsm.py")
SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "SMSocket", "dsm.py")
SHARED = ["fingerprint", "_same_session", "canon_mem", "canon_cons", "budget_map",
          "SchemaStore", "validate", "encode_turn", "decode_turn", "_turn_of",
          "_inline", "to_openai", "to_responses", "to_anthropic", "split_fan",
          "leak_check", "name_collision", "_norm_tool_call", "decode_to_openai_shape",
          "merge_stream"]
CONSTS = ["ROLE", "RID", "POLICY_KEYS", "CORE", "REASON_TIER"]


def _src(p):
    return io.open(p, encoding="utf-8").read()


def nodes(src):
    """top-level def/class name -> AST dump (comments and blank lines can't drift it)."""
    out = {}
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            out[n.name] = ast.dump(ast.fix_missing_locations(n), annotate_fields=False)
    return out


def consts(src):
    out = {}
    for c in CONSTS:
        m = re.search(r"^%s *=.*$" % c, src, re.M)
        out[c] = m.group(0) if m else None
    return out


def test_dsm_codec_is_a_byte_identical_copy():
    if not os.path.exists(CLIENT):
        import pytest
        pytest.skip("SMS core not installed at %s" % CLIENT)
    csrc, ssrc = _src(CLIENT), _src(SERVER)
    a, b = nodes(csrc), nodes(ssrc)
    ca, cb = consts(csrc), consts(ssrc)
    missing = [n for n in SHARED if n not in a or n not in b]
    assert not missing, "shared node vanished (rename? update SHARED + the generator): %s" % missing
    drift = [n for n in SHARED if n not in missing and a[n] != b[n]]
    assert not drift, ("§3 violated: %s differs between repos - regenerate the server "
                       "copy with tools/gen_server_dsm.py instead of hand-editing" % drift)
    bad_const = [c for c in CONSTS if ca[c] != cb[c] or ca[c] is None]
    assert not bad_const, "constants drifted: %s" % bad_const


def test_server_keeps_client_only_concerns_out():
    """服务端不得混入客户端专属逻辑（链读取/开关/settings 都属 SMS 侧）。"""
    src = _src(SERVER)
    for banned in ("import chains", "import settings", "import resolve_home",
                   "import atomic_io", "def build_env", "def cli_", "def mem_ids"):
        assert banned not in src, "client-only concern leaked into the server codec: %s" % banned


def test_server_mem_resolution_is_explicitly_unsupported():
    """链在服务端不存在：mem 必须解析失败并计数，绝不静默当成空记忆。"""
    from SMSocket import dsm
    before = dsm.mem_unsupported()
    assert dsm.canon_mem(["abcdef0123", "1234567890"]) == ""
    assert dsm.mem_unsupported() > before
