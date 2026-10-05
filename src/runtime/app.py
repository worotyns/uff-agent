from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.agent import AgentProfile, load_agents
from src.bootstrap import send_welcome_email, welcome_lock
from src.config import Config
from src.event import EventQueue
from src.llm.embeddings import EMBEDDING_MODEL
from src.llm.openrouter import OpenRouterClient
from src.mail.listener import IMAPListener
from src.mail.sender import SMTPSender
from src.runtime.dispatcher import process_event
from src.runtime.runner import AgentRunner
from src.runtime.scheduler import Scheduler
from src.runtime.server.health import start_health_server
from src.storage.background_task import BackgroundTaskStore
from src.storage.memory import MemoryStore
from src.storage.thread import ThreadStore
from src.tools.mcp import McpManager
from src.tools.skill import SkillRegistry

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    data_root: Path
    root: Path
    config: Config
    agents: dict[str, AgentProfile]
    llm: OpenRouterClient
    queue: EventQueue
    scheduler: Scheduler
    thread_store: ThreadStore
    memory: MemoryStore
    skills: SkillRegistry
    background_tasks: BackgroundTaskStore
    mcp: McpManager
    runner: AgentRunner
    smtp: SMTPSender | None = None
    listener: IMAPListener | None = None

    @classmethod
    def build(cls, data_root: Path, root: Path) -> "Runtime":
        config = Config(primary=data_root, fallback=root)
        agents = load_agents(primary=data_root, fallback=root, agent_names=config.agent_names)
        llm = OpenRouterClient(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            referer=f"https://{config.agent_domain}" if config.agent_domain else "",
        )
        queue = EventQueue(data_root / "events")
        scheduler = Scheduler(queue, config.heartbeat_interval, config.dream_hour, memory_root=data_root)
        thread_store = ThreadStore(data_root)
        memory = MemoryStore(data_root)
        skills = SkillRegistry(root)
        background_tasks = BackgroundTaskStore(data_root)
        mcp = McpManager(data_root, enabled=config.mcp_enabled)
        runner = AgentRunner(llm, skills, mcp)

        smtp: SMTPSender | None = None
        email_config = config.email_config
        if email_config and email_config.get("smtp", {}).get("host"):
            smtp = SMTPSender(email_config)

        listener: IMAPListener | None = None
        if email_config and email_config.get("imap", {}).get("host"):
            listener = IMAPListener(email_config, queue, thread_store=thread_store, data_root=data_root)

        return cls(
            data_root=data_root,
            root=root,
            config=config,
            agents=agents,
            llm=llm,
            queue=queue,
            scheduler=scheduler,
            thread_store=thread_store,
            memory=memory,
            skills=skills,
            background_tasks=background_tasks,
            mcp=mcp,
            runner=runner,
            smtp=smtp,
            listener=listener,
        )

    def log_summary(self) -> None:
        logger.info("loaded agents: %s", list(self.agents.keys()))
        for name, a in self.agents.items():
            logger.info("  %s: model=%s skills=%s", name, a.model, a.skills)
        logger.info("LLM: model=%s", self.config.openrouter_model)
        logger.info("config: %s", self.config.root)
        logger.info("data root: %s", self.data_root)
        logger.info("embeddings: model=%s", EMBEDDING_MODEL)

    def start_background(self) -> None:
        if self.listener:
            t = threading.Thread(target=self.listener.idle_loop, daemon=True, name="imap")
            t.start()
            logger.info("IMAP listener started")

        def _scheduler_loop() -> None:
            while True:
                self.scheduler.tick()
                time.sleep(1)

        t = threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler")
        t.start()
        logger.info("scheduler started (heartbeat every %s s)", self.config.heartbeat_interval)

        start_health_server()

        if self.smtp and self.config.welcome_email and not welcome_lock(self.data_root).exists():
            owner_email = self.config.email_config.get("notify_email", "")
            if owner_email:
                send_welcome_email(self.smtp, owner_email, self.data_root, self.root, language=self.config.user_language)

    def run(self) -> None:
        running = True

        def _stop(*args: object) -> None:
            nonlocal running
            running = False
            logger.info("shutting down")

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        try:
            while running:
                event = self.queue.poll()
                if event:
                    process_event(event, self)
                else:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass

        logger.info("stopped")
