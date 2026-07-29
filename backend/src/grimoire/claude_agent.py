"""Claude Agent SDK provider — routes prompts through the local Claude Code login.

Auth is inherited from the host's Claude Code session (or CLAUDE_CODE_OAUTH_TOKEN);
usage bills against the owner's Claude subscription, not an API key. See
docs/superpowers/specs/2026-07-10-claude-provider-design.md for the policy notes.
"""

from __future__ import annotations

from typing import AsyncIterator

from .llm_errors import LLMError


class ClaudeAgentError(LLMError):
    pass


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _flatten(turns: list[dict]) -> str:
    # The Agent SDK takes a single prompt string, not a message array; render
    # the conversation as a transcript and cue the next assistant reply.
    lines = [f"[{m['role']}]\n{m['content']}" for m in turns]
    lines.append("[assistant]")
    return "\n\n".join(lines)


class ClaudeAgentClient:
    async def stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        try:
            from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                          CLINotFoundError, ProcessError, TextBlock, query)
        except ImportError as exc:
            raise ClaudeAgentError(
                "missing_dependency",
                "claude-agent-sdk is not installed — pip install 'grimoire[claude]'",
            ) from exc
        system, turns = _split_system(messages)
        options = ClaudeAgentOptions(
            system_prompt=system or None, model=model, allowed_tools=[], max_turns=1,
        )
        try:
            async for message in query(prompt=_flatten(turns), options=options):
                # Proof of life for the facade's idle bound: the SDK sends
                # messages that carry no text (thinking, tool, result), and a
                # model can spend minutes on those before its first word (#243).
                yield ""
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield block.text
        except CLINotFoundError as exc:
            raise ClaudeAgentError("missing_dependency", str(exc)) from exc
        except ProcessError as exc:
            raise ClaudeAgentError("bad_response", str(exc)) from exc
        # so a future SDK-raised ClaudeAgentError isn't re-tagged as "network" below
        except ClaudeAgentError:
            raise
        except Exception as exc:
            raise ClaudeAgentError("network", str(exc)) from exc

    async def complete(self, messages: list[dict], model: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model)])
