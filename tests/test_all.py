from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Config
def test_config_loads():
    from src.config import Config
    root = Path(".")
    c = Config(root)
    assert "${OPENROUTER_KEY}" in c.openrouter_api_key or c.openrouter_api_key == ""
    assert c.openrouter_model
    assert c.heartbeat_interval > 0


def test_config_interpolates_env():
    os.environ["TEST_KEY"] = "test-value"
    from src.config import load_yaml
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("key: ${TEST_KEY}\n")
        f.flush()
        data = load_yaml(f.name)
        assert data["key"] == "test-value"
    os.unlink(f.name)


def test_config_user_language():
    os.environ["USER_LANGUAGE"] = "en"
    os.environ["USER_TIMEZONE"] = "UTC"
    from src.config import Config
    c = Config(".")
    assert c.user_language == "en"


# Event system
def test_event_queue():
    from src.event import Event, EventQueue
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue(tmp)
        e = Event(type="test", agent="assistant", message="hello")
        q.push(e)
        assert q.pending_count() == 1
        polled = q.poll()
        assert polled is not None
        assert polled.type == "test"
        assert polled.message == "hello"
        q.mark_done(polled)
        assert q.pending_count() == 0


# Agent profiles
def test_load_agents():
    import os
    os.environ["ASSISTANT_MODEL"] = "deepseek/deepseek-v4.1-flash"
    os.environ["RESEARCHER_MODEL"] = "deepseek/deepseek-v4-pro"
    from src.agent import load_agents
    from pathlib import Path
    root = Path(".")
    agents = load_agents(primary=root, fallback=root, agent_names=["assistant", "researcher"])
    assert "assistant" in agents
    assert "researcher" in agents
    assert agents["assistant"].model == "deepseek/deepseek-v4.1-flash"
    assert agents["researcher"].model == "deepseek/deepseek-v4-pro"
    assert "rag" in agents["assistant"].skills
    assert "web" in agents["assistant"].skills


# Memory
def test_memory_store():
    from src.storage.memory import MemoryStore
    with tempfile.TemporaryDirectory() as tmp:
        m = MemoryStore(Path(tmp))
        m.append("facts", "test fact")
        content = m.read("facts")
        assert "test fact" in content


def test_memory_search_rg():
    from src.storage.memory_search import rg_search
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        m = root / "memory"
        m.mkdir()
        (m / "facts.md").write_text("- [2026-01-01] wynajmuje mieszkanie na Woli\n")
        (m / "decisions.md").write_text("- [2026-02-01] bank na Revolut\n")
        (m / "2026-08-12.md").write_text("- [2026-08-12] przedłużenie umowy najmu\n")
        res = rg_search(root, "najmu")
        assert len(res) >= 2
        res2 = rg_search(root, "revolut", kinds=("decisions",))
        assert len(res2) == 1 and "Revolut" in res2[0]["text"]
        assert rg_search(root, "zzznope") == []


def test_ontology_graph_cache_write_through():
    import importlib
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AGENT_HOME"] = tmp
        import skills.ontology.tool as onto
        importlib.reload(onto)

        r1 = onto.entity_create("Person", {"name": "Mateusz"})
        eid = r1.split("[")[1].split("]")[0]
        assert "Mateusz" in onto.entity_query("Person")

        r2 = onto.entity_create("Asset", {"name": "Mieszkanie"})
        aid = r2.split("[")[1].split("]")[0]
        onto.entity_relate(eid, "owns", aid)
        g = onto.entity_get(eid)
        assert "owns" in g and "Mieszkanie" in g

        onto.entity_update(eid, {"email": "n@x.pl"})
        assert "n@x.pl" in onto.entity_get(eid)

        onto.entity_remove(aid)
        assert "Mieszkanie" not in onto.entity_query("Asset")


def _onto_setup():
    import importlib
    tmp = tempfile.TemporaryDirectory()
    os.environ["AGENT_HOME"] = tmp.name
    import skills.ontology.tool as onto
    importlib.reload(onto)
    return tmp, onto


def _create(onto, e_type, properties, **meta):
    r = onto.entity_create(e_type, properties, **meta)
    eid = r.split("[")[1].split("]")[0]
    return eid


def test_ontology_schema_is_generic_and_permissive():
    tmp, onto = _onto_setup()
    try:
        schema = onto._load_schema()
        types = schema.get("types", {})
        for t in ("Person", "Company", "Document", "Event", "Fact", "Note", "Plan", "Task", "Asset", "Place", "Interest", "Project"):
            assert t in types
        # unknown types + shapes used by real agents must pass validation
        assert onto._validate("Plan", {"name": "Plan A", "opis": "x"}) is None
        assert onto._validate("CustomType", {"whatever": 1}) is None
        # generic relations never restrict
        assert onto._validate_relation("refers_to", "Document", "Document") is None
        assert onto._validate_relation("relates_to", "Anything", "Anything") is None
        assert onto._validate_relation("unknown_rel", "A", "B") is None
    finally:
        tmp.cleanup()


def test_ontology_okf_metadata_lifecycle():
    tmp, onto = _onto_setup()
    try:
        eid = _create(onto, "Person", {"name": "Mateusz", "address": "Kraków"},
                      tags=["rodzina"], status="stable",
                      stale_after="2030-01-01",
                      verified=[{"by": "human:user", "at": "2026-08-13"}],
                      sources=["mail"])
        g = onto.entity_get(eid)
        assert "Tagi: rodzina" in g
        assert "Status: stable" in g
        assert "Ważna do: 2030-01-01" in g
        assert "Zweryfikowana" in g
        assert "Źródła" in g

        # stale detection
        assert onto._is_stale(onto._load_graph()["entities"][eid]) is False

        # update metadata
        onto.entity_update(eid, {"address": "Warszawa"}, status="deprecated", stale_after="2020-01-01")
        ent = onto._load_graph()["entities"][eid]
        assert ent["properties"]["address"] == "Warszawa"
        assert ent["status"] == "deprecated"
        assert onto._is_stale(ent) is True

        # metadata survives a fresh reload (replay from file)
        import importlib
        importlib.reload(onto)
        g2 = onto.entity_get(eid)
        assert "Warszawa" in g2 and "deprecated" in g2 and "2020-01-01" in g2
    finally:
        tmp.cleanup()


def test_ontology_query_filters_and_stale_marker():
    tmp, onto = _onto_setup()
    try:
        _create(onto, "Fact", {"name": "stary", "category": "task"}, stale_after="2020-01-01")
        _create(onto, "Fact", {"name": "świeży", "category": "preference"})
        out = onto.entity_query("Fact")
        assert "świeży" in out
        assert "przeterminowana" in out  # the stale one gets a marker

        filtered = onto.entity_query("Fact", filters={"category": "preference"})
        assert "świeży" in filtered and "stary" not in filtered
    finally:
        tmp.cleanup()


def test_ontology_graph_traversal():
    tmp, onto = _onto_setup()
    try:
        pid = _create(onto, "Person", {"name": "Mateusz"})
        cid = _create(onto, "Company", {"name": "PushPushGo"})
        onto.entity_relate(pid, "works_at", cid)
        g = onto.entity_graph(pid)
        assert "Mateusz" in g and "PushPushGo" in g and "works_at" in g
    finally:
        tmp.cleanup()


def test_send_email_starts_new_thread():
    from src.thread_ctx import RequestContext, activate

    class FakeSMTP:
        def __init__(self):
            self.notify_to = "owner@x.pl"
            self.calls = []

        def send(self, **kw):
            self.calls.append(kw)
            return "mid-123"

    fake = FakeSMTP()
    ctx = RequestContext(smtp=fake, sender="owner@x.pl", subject="stary wątek", in_reply_to="old-mid")
    with activate(ctx):
        from skills.email.tool import SendEmail
        out = SendEmail().execute(subject="Nowy temat", body="Treść")
        assert "mid-123" in out
        assert len(fake.calls) == 1
        assert fake.calls[0]["subject"] == "Nowy temat"
        assert fake.calls[0]["to"] == "owner@x.pl"
        assert fake.calls[0].get("in_reply_to") is None  # new thread, not a reply


# Email security: allowed_senders
def test_is_allowed_exact_match():
    from src.mail.parser import is_allowed
    assert is_allowed("user@example.com", ["user@example.com"])
    assert not is_allowed("other@example.com", ["user@example.com"])


def test_is_allowed_domain_wildcard():
    from src.mail.parser import is_allowed
    assert is_allowed("user@example.com", ["@example.com"])
    assert is_allowed("admin@sub.example.com", ["@example.com"]) is False
    assert not is_allowed("user@other.com", ["@example.com"])


def test_is_allowed_star_wildcard():
    from src.mail.parser import is_allowed
    assert is_allowed("anyone@example.com", ["*"])
    assert is_allowed("foo@bar.com", ["*"])


def test_is_allowed_name_brackets():
    from src.mail.parser import extract_email, is_allowed
    assert extract_email("John <john@doe.com>") == "john@doe.com"
    assert extract_email("plain@email.com") == "plain@email.com"
    assert is_allowed("Jan Kowalski <jan@example.com>", ["jan@example.com"])


def test_email_config_defaults_allowed_senders():
    from src.config import Config
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "config.yaml").write_text(
            "email:\n  notify_email: owner@test.pl\n  smtp:\n    host: smtp.test.pl\n    user: bot@test.pl\n    password: x\n"
        )
        config = Config(d)
        ecfg = config.email_config
        assert "allowed_senders" in ecfg
        assert "owner@test.pl" in ecfg["allowed_senders"]


# Tasks
def test_task_store():
    from src.storage.tasks import TaskStore
    with tempfile.TemporaryDirectory() as tmp:
        s = TaskStore(Path(tmp))
        s.add("reminder", "Buy milk", time="2026-07-07T10:00:00Z")
        s.add("todo", "Write")
        assert len(s.list()) == 2
        due = s.get_due(now="2026-07-07T12:00:00Z")
        assert len(due) == 1
        s.complete(due[0]["id"])
        assert len(s.list(done=False)) == 1


def test_hourly_reminder_recurrence():
    from src.storage.tasks import TaskStore
    with tempfile.TemporaryDirectory() as tmp:
        s = TaskStore(Path(tmp))
        t = s.add("reminder", "Weather", time="2026-07-07T13:00:00Z", recurrence="hourly")
        tid = t["id"]
        due = s.get_due(now="2026-07-07T13:00:00Z")
        assert len(due) == 1
        assert due[0]["id"] == tid
        s.complete_and_reschedule(tid)
        tasks = s.list(done=False)
        assert len(tasks) == 1
        assert tasks[0]["time"] == "2026-07-07T14:00:00Z"


def test_reminder_without_time_due_immediately():
    from src.storage.tasks import TaskStore
    with tempfile.TemporaryDirectory() as tmp:
        s = TaskStore(Path(tmp))
        s.add("reminder", "No time")
        due = s.get_due(now="2026-07-07T00:00:00Z")
        assert len(due) == 1


def test_reminder_mode_thread_and_new():
    from src.storage.tasks import TaskStore
    with tempfile.TemporaryDirectory() as tmp:
        s = TaskStore(Path(tmp))
        t1 = s.add("reminder", "in-thread", time="2026-07-07T10:00:00Z", mode="thread")
        t2 = s.add("reminder", "fresh-thread", time="2026-07-07T10:00:00Z", mode="new")
        assert t1["mode"] == "thread"
        assert t2["mode"] == "new"
        # invalid mode falls back to thread
        t3 = s.add("reminder", "bogus", mode="weird")
        assert t3["mode"] == "thread"


def test_hourly_cycles_correctly():
    from src.storage.tasks import _next_time
    assert _next_time("2026-07-07T10:00:00Z", "hourly") == "2026-07-07T11:00:00Z"
    assert _next_time("2026-07-07T23:00:00Z", "hourly") == "2026-07-08T00:00:00Z"


def test_hourly_reminder_cycles():
    from src.storage.tasks import TaskStore
    with tempfile.TemporaryDirectory() as tmp:
        s = TaskStore(Path(tmp))
        t = s.add("reminder", "Test", time="2026-07-07T10:00:00Z", recurrence="hourly")
        tid = t["id"]
        for expected in ("10:00", "11:00", "12:00", "13:00"):
            due = s.get_due(now=f"2026-07-07T{expected}:00Z")
            assert len(due) == 1
            assert due[0]["time"] == f"2026-07-07T{expected}:00Z"
            s.complete_and_reschedule(tid)
        tasks = s.list(done=False)
        assert len(tasks) == 1
        assert tasks[0]["time"] == "2026-07-07T14:00:00Z"


# Threads
def test_thread_store():
    from src.storage.thread import ThreadStore
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        s = ThreadStore(root)
        s.append_message("thread-1", "user", "Hello", agent="assistant")
        s.append_message("thread-1", "assistant", "Hi")
        msgs = s.get_messages("thread-1")
        assert len(msgs) == 2
        meta = s.get_meta("thread-1")
        assert meta.get("agent") == "assistant"


def test_thread_source_tracking():
    from src.storage.thread import ThreadStore
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        s = ThreadStore(root)
        s.ensure("thread-src", "assistant")
        sources = [
            {"tool": "web_search", "args": {"query": "PKB Polski"}, "result_snippet": "Wynik...", "took_s": 0.5},
            {"tool": "web_fetch", "args": {"url": "https://example.com"}, "result_snippet": "Dane...", "took_s": 1.2},
        ]
        s.record_tool_calls("thread-src", sources)
        stored = s.get_sources("thread-src")
        assert len(stored) == 2
        assert stored[0]["tool"] == "web_search"
        assert stored[0]["args"]["query"] == "PKB Polski"
        assert stored[0]["time"] is not None
        assert stored[1]["tool"] == "web_fetch"


def test_thread_source_tracking_empty():
    from src.storage.thread import ThreadStore
    with tempfile.TemporaryDirectory() as tmp:
        s = ThreadStore(Path(tmp))
        assert s.get_sources("nonexistent") == []
        s.record_tool_calls("nonexistent", [])
        assert s.get_sources("nonexistent") == []


def testformat_sources_block():
    from src.runtime.sources import format_sources_block
    sources = [
        {"tool": "web_search", "args": {"query": "PKB Polski", "_thread_id": "x"}, "result_snippet": "**URL:** https://example.com\n..."},
        {"tool": "web_fetch", "args": {"url": "https://example.com"}, "result_snippet": "..."},
    ]
    block = format_sources_block(sources)
    assert "Sources" in block
    assert "web_search" in block
    assert "web_fetch" in block
    assert "https://example.com" in block
    assert "_thread_id" not in block


def test_format_sources_block_deduplicates():
    from src.runtime.sources import format_sources_block
    sources = [
        {"tool": "web_search", "args": {"query": "test"}, "result_snippet": "x"},
        {"tool": "web_search", "args": {"query": "test"}, "result_snippet": "y"},
    ]
    block = format_sources_block(sources)
    assert block.count("web_search") == 1
    assert "×2" in block


def test_format_sources_block_search_documents():
    from src.runtime.sources import format_sources_block
    sources = [
        {"tool": "search_documents", "args": {"query": "test query", "scope": "global"}, "result_snippet": "Found 2 results:\n[attachments/Umowa.pdf] (score: 0.85)\ntext\n\n[attachments/Polisa.pdf] (score: 0.72)\ntext"},
    ]
    block = format_sources_block(sources)
    assert "Umowa.pdf" in block
    assert "Polisa.pdf" in block
    assert "test query" not in block


def test_format_sources_block_empty():
    from src.runtime.sources import format_sources_block
    assert format_sources_block([]) == ""


# Routing
def test_router():
    from src.mail.router import resolve_agent
    assert resolve_agent("bot+researcher@uff.email") == "researcher"
    assert resolve_agent("bot+assistant@uff.email") == "assistant"
    assert resolve_agent("bot@uff.email") == "assistant"


# Skills
def test_skill_loader():
    from src.tools.skill import SkillRegistry
    from pathlib import Path
    skills = SkillRegistry(Path("."))
    assert len(skills.list_skills()) >= 10
    tools = skills.get_tools()
    assert all("function" in t for t in tools)


# Tokenizer
def test_tokenizer():
    from src.tokenizer import estimate_tokens, trim_to_budget, get_model_fallback
    assert estimate_tokens("hello world") > 0
    msgs = [{"role": "user", "content": "hello"}]
    trimmed = trim_to_budget(msgs, "deepseek/deepseek-v4.1-flash")
    assert len(trimmed) == 1
    assert get_model_fallback("deepseek/deepseek-v4-pro") == "deepseek/deepseek-v4.1-flash"


# Context builder
def test_context():
    from src.config import Config
    from src.agent import AgentProfile
    from src.context import build_context
    from pathlib import Path
    config = Config(".")
    agent = AgentProfile(name="test", soul="You are test.", model="deepseek/deepseek-v4.1-flash", skills=[], config={})
    ctx = build_context(agent, config, messages=[{"role": "user", "content": "hello"}])
    assert len(ctx) == 2
    assert ctx[0]["role"] == "system"
    assert "test" in ctx[0]["content"]


# Welcome email rendering
def test_welcome_email_rendering():
    from pathlib import Path
    import os

    root = Path(".")
    template_path = root / "welcome.en.md"
    assert template_path.exists(), "welcome.en.md not found"

    template = template_path.read_text()
    agent_email = "bot@uff.email"
    agent_handle = "bot"

    body = (
        template
        .replace("{{agent_email}}", agent_email)
        .replace("{{agent}}", agent_handle)
        .strip()
    )

    assert "Hi" in body
    assert "bot@uff.email" in body
    assert "$4.99/month" in body
    assert "support@uff.email" in body
    assert body.strip().endswith("uff.email")

    from src.mail.sender import md_to_html
    html = md_to_html(body)
    assert "<p>" in html
    assert "bot@uff.email" in html
    assert "<strong>" in html


# InsufficientCreditsError propagation
def test_insufficient_credits():
    from src.llm.openrouter import InsufficientCreditsError
    err = InsufficientCreditsError("insufficient credits for all models")
    assert "credits" in str(err)
    assert isinstance(err, RuntimeError)


# Data analysis tools
def test_json_query_len():
    from skills.data_analysis.tool import JsonQuery
    jq = JsonQuery()
    result = jq.execute(data='[{"x": 1}, {"x": 2}]', expression="len(data)")
    assert result == "2"


def test_json_query_filter():
    from skills.data_analysis.tool import JsonQuery
    jq = JsonQuery()
    result = jq.execute(data='[{"n": "a", "v": 10}, {"n": "b", "v": 20}]', expression="[x['n'] for x in data if x['v'] > 15]")
    assert '"b"' in result
    assert '"a"' not in result


def test_json_query_single_value():
    from skills.data_analysis.tool import JsonQuery
    jq = JsonQuery()
    result = jq.execute(data='{"name": "test", "count": 42}', expression="data['count']")
    assert "42" in result


def test_json_query_bad_json():
    from skills.data_analysis.tool import JsonQuery
    jq = JsonQuery()
    result = jq.execute(data="not json", expression="len(data)")
    assert "Błąd parsowania JSON" in result


def test_json_query_bad_expression():
    from skills.data_analysis.tool import JsonQuery
    jq = JsonQuery()
    result = jq.execute(data="[]", expression="undefined_var")
    assert "Błąd wyrażenia" in result


def test_sql_query_basic():
    from skills.data_analysis.tool import SqlQuery
    sq = SqlQuery()
    result = sq.execute(sql="SELECT count(*) AS cnt FROM data", csv_data="id,name\n1,foo\n2,bar\n3,baz")
    assert "3" in result
    assert "cnt" in result


def test_sql_query_filter():
    from skills.data_analysis.tool import SqlQuery
    sq = SqlQuery()
    result = sq.execute(sql="SELECT name FROM data WHERE CAST(value AS REAL) > 15", csv_data="name,value\na,10\nb,20\nc,30")
    assert '"b"' in result
    assert '"c"' in result
    assert '"a"' not in result


def test_sql_query_empty_csv():
    from skills.data_analysis.tool import SqlQuery
    sq = SqlQuery()
    result = sq.execute(sql="SELECT * FROM data", csv_data="col\n")
    assert "nie zawiera danych" in result


def test_sql_query_no_sql():
    from skills.data_analysis.tool import SqlQuery
    sq = SqlQuery()
    result = sq.execute(sql="", csv_data="x\n1")
    assert "Podaj zapytanie" in result


def test_duckdb_query_not_installed():
    from skills.data_analysis.tool import DuckDbQuery
    dq = DuckDbQuery()
    result = dq.execute(sql="SELECT 1", files={})
    assert "nie jest zainstalowane" in result or "Zwrocono" in result


def test_markdown_tables():
    from src.mail.sender import md_to_html
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = md_to_html(md)
    assert "<table>" in html
    assert "<th>A</th>" in html or "<th>A" in html
    assert "<td>1</td>" in html or ">1<" in html


# Forward detection
def test_parse_email_forward_by_subject():
    from src.mail.parser import parse_email
    raw = (
        b"From: user@test.com\r\n"
        b"To: bot@uff.email\r\n"
        b"Subject: Fwd: pilne pytanie\r\n"
        b"Message-ID: <fwd-001@test.com>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"Co o tym myslisz?"
    )
    parsed = parse_email(raw)
    assert parsed["is_forward"] is True


def test_parse_email_forward_by_mime():
    from src.mail.parser import parse_email
    raw = (
        b"From: user@test.com\r\n"
        b"To: bot@uff.email\r\n"
        b"Subject: sprawdz to\r\n"
        b"Message-ID: <fwd-mime-001@test.com>\r\n"
        b"Content-Type: message/rfc822\r\n"
        b"\r\n"
        b"From: alice@test.com\r\n"
        b"Subject: oryginalny\r\n"
        b"Message-ID: <orig-001@test.com>\r\n"
        b"\r\n"
        b"Tresc oryginalna."
    )
    parsed = parse_email(raw)
    assert parsed["is_forward"] is True


def test_parse_email_not_forward():
    from src.mail.parser import parse_email
    raw = (
        b"From: user@test.com\r\n"
        b"To: bot@uff.email\r\n"
        b"Subject: pytanie\r\n"
        b"Message-ID: <normal-001@test.com>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"Mam pytanie."
    )
    parsed = parse_email(raw)
    assert parsed["is_forward"] is False


def test_send_reply_forward_no_threading():
    from src.runtime.mailout import send_email_reply
    captured = {}

    class FakeSmtp:
        def send(self, **kw):
            captured.update(kw)
            return "mid-123"

    class FakeThreadStore:
        def add_message_id(self, thread_id, mid):
            pass

    smtp = FakeSmtp()
    meta = {"message_id": "fwd-002@test", "in_reply_to": "orig-001@test", "is_forward": True}
    event_mock = type("Event", (), {"thread": "t1"})()
    send_email_reply(smtp, "user@test.com", "Fwd: sprawdz", "odpowiedz",
                     meta, event_mock, FakeThreadStore(), None)
    assert "in_reply_to" not in captured, "forward should NOT set In-Reply-To"
    assert "references" not in captured, "forward should NOT set References"
    assert captured.get("subject") == "Bot: sprawdz"


def test_send_reply_normal_sets_threading():
    from src.runtime.mailout import send_email_reply
    captured = {}

    class FakeSmtp:
        def send(self, **kw):
            captured.update(kw)
            return "mid-123"

    class FakeThreadStore:
        def add_message_id(self, thread_id, mid):
            pass

    smtp = FakeSmtp()
    meta = {"message_id": "msg-001@test", "in_reply_to": "", "is_forward": False}
    event_mock = type("Event", (), {"thread": "t2"})()
    send_email_reply(smtp, "user@test.com", "pytanie", "odpowiedz",
                     meta, event_mock, FakeThreadStore(), None)
    assert captured.get("in_reply_to") == "msg-001@test"
    assert captured.get("references") == "msg-001@test"
    assert captured.get("subject") == "Odp: pytanie"


# File security: sensitive path blocking
def test_is_sensitive_blocks_dotenv():
    from skills.files.tool import _is_sensitive
    assert _is_sensitive(".env")
    assert _is_sensitive("/app/brain/.env")


def test_is_sensitive_blocks_config_yaml():
    from skills.files.tool import _is_sensitive
    assert _is_sensitive("config.yaml")
    assert _is_sensitive("/app/brain/config.yaml")
    assert _is_sensitive("config.yml")


def test_is_sensitive_allows_normal_files():
    from skills.files.tool import _is_sensitive
    assert not _is_sensitive("notes.txt")
    assert not _is_sensitive("report.pdf")
    assert not _is_sensitive("threads/abc123/attachments/file.pdf")


def test_safe_path_blocks_sensitive():
    from skills.files.tool import _safe_path
    assert _safe_path(".env") is None
    assert _safe_path("config.yaml") is None
    assert _safe_path("config.yml") is None


def test_find_file_blocks_sensitive():
    from skills.files.tool import _find_file
    assert _find_file(".env") is None
    assert _find_file("config.yaml") is None


# Terminal security: command blocklist
def test_check_blocked_blocks_cat_dotenv():
    from skills.terminal.tool import RunCommand
    assert RunCommand._check_blocked("cat .env")
    assert RunCommand._check_blocked("cat /app/brain/.env")


def test_check_blocked_blocks_cat_config():
    from skills.terminal.tool import RunCommand
    assert RunCommand._check_blocked("cat config.yaml")
    assert RunCommand._check_blocked("less config.yaml")


def test_check_blocked_allows_safe_commands():
    from skills.terminal.tool import RunCommand
    assert RunCommand._check_blocked("pip install requests") is None
    assert RunCommand._check_blocked("ls -la") is None
    assert RunCommand._check_blocked("python script.py") is None


# Speech security: transcribe_audio path check
def test_transcribe_audio_blocks_sensitive():
    from skills.speech.tool import TranscribeAudio
    ta = TranscribeAudio()
    result = ta.execute(path="/app/.env")
    assert "Access denied" in result

    result2 = ta.execute(path="/app/config.yaml")
    assert "Access denied" in result2


# Email redaction: _redact_sensitive
def test_redact_sensitive_removes_env_values():
    import os
    os.environ["OPENROUTER_KEY"] = "sk-or-v1-test12345"
    from src.mail.sender import _redact_sensitive
    result = _redact_sensitive("my key is sk-or-v1-test12345 and it's secret")
    assert "[REDACTED]" in result
    assert "sk-or-v1-test12345" not in result


def test_redact_sensitive_handles_empty_env():
    import os
    os.environ.pop("OPENROUTER_KEY", None)
    from src.mail.sender import _redact_sensitive
    result = _redact_sensitive("no secrets here")
    assert result == "no secrets here"
