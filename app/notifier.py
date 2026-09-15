"""
Telegram approval layer.

THE ASYMMETRY THAT MATTERS:

    ENTRIES require your approval.   Opening risk is opt-in.
    EXITS fire automatically.        Reducing risk never waits for a human.

If a stop triggers at 3am while you're asleep, waiting for a tap is precisely
how an account gets destroyed. So exits notify you; they do not ask you.

Proposals also EXPIRE. A memecoin signal that is ten minutes old is not the
same trade any more, and silently executing a stale one is worse than missing it.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
import requests

API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class Proposal:
    pid: str
    symbol: str
    token_address: str
    size_usd: float
    price: float
    composite: float
    scores: dict
    rationale: list[str]
    liquidity: float
    round_trip_pct: float
    created: float = field(default_factory=time.time)
    status: str = "pending"          # pending | approved | rejected | expired
    message_id: int | None = None

    def age_s(self) -> float:
        return time.time() - self.created

    def expired(self, ttl: float) -> bool:
        return self.status == "pending" and self.age_s() > ttl


class Notifier:
    def __init__(self, token: str, chat_id: str, enabled: bool = True, timeout: int = 20):
        self.token, self.chat_id = token, chat_id
        self.enabled = enabled and bool(token and chat_id)
        self.timeout = timeout
        self.offset = 0
        self.s = requests.Session()

    def _call(self, method: str, **payload):
        if not self.enabled:
            return None
        try:
            r = self.s.post(API.format(token=self.token, method=method),
                            json=payload, timeout=self.timeout)
            return r.json()
        except Exception:
            return None

    # -- outbound -------------------------------------------------------
    def send(self, text: str) -> int | None:
        d = self._call("sendMessage", chat_id=self.chat_id, text=text,
                       parse_mode="HTML", disable_web_page_preview=True)
        try:
            return d["result"]["message_id"]
        except (TypeError, KeyError):
            return None

    def propose(self, p: Proposal, ttl_min: float) -> int | None:
        why = "\n".join(f"  • {r}" for r in p.rationale[:6])
        txt = (
            f"<b>ENTRY PROPOSAL — {p.symbol}</b>\n\n"
            f"<b>Size</b>  ${p.size_usd:,.2f}\n"
            f"<b>Price</b> ${p.price:.8f}\n"
            f"<b>Score</b> {p.composite:.1f}/100\n"
            f"<b>Liquidity</b> ${p.liquidity:,.0f}\n"
            f"<b>Round-trip cost</b> {p.round_trip_pct:.2f}%\n"
            f"<b>Needs</b> +{p.round_trip_pct:.2f}% just to break even\n\n"
            f"<b>Why:</b>\n{why}\n\n"
            f"<i>Expires in {ttl_min:.0f} min. No tap = no trade.</i>")
        d = self._call("sendMessage", chat_id=self.chat_id, text=txt,
                       parse_mode="HTML", disable_web_page_preview=True,
                       reply_markup={"inline_keyboard": [[
                           {"text": "✓ Approve", "callback_data": f"y:{p.pid}"},
                           {"text": "✗ Reject", "callback_data": f"n:{p.pid}"}]]})
        try:
            p.message_id = d["result"]["message_id"]
            return p.message_id
        except (TypeError, KeyError):
            return None

    def resolve(self, p: Proposal, verdict: str, detail: str = "") -> None:
        """Replace the buttons once a proposal is settled, so it can't be tapped twice."""
        if p.message_id:
            self._call("editMessageReplyMarkup", chat_id=self.chat_id,
                       message_id=p.message_id, reply_markup={"inline_keyboard": []})
        self.send(f"<b>{p.symbol}</b> — {verdict}" + (f"\n{detail}" if detail else ""))

    def exit_notice(self, symbol: str, reason: str, net_usd: float, net_pct: float,
                    sig: str = "") -> None:
        icon = "▲" if net_usd > 0 else "▼"
        link = f"\n<a href='https://solscan.io/tx/{sig}'>tx</a>" if sig and len(sig) > 20 else ""
        self.send(f"<b>EXIT {icon} {symbol}</b>\n{reason}\n"
                  f"Net <b>${net_usd:+,.2f}</b> ({net_pct:+.2f}%)"
                  f"\n<i>Exits are automatic — never held for approval.</i>{link}")

    def alert(self, title: str, body: str) -> None:
        self.send(f"<b>{title}</b>\n{body}")

    # -- inbound --------------------------------------------------------
    def poll(self) -> list[tuple[str, str, str]]:
        """Returns [(action, pid, callback_id)] for each button press."""
        if not self.enabled:
            return []
        try:
            r = self.s.get(API.format(token=self.token, method="getUpdates"),
                           params={"offset": self.offset, "timeout": 0},
                           timeout=self.timeout)
            d = r.json()
        except Exception:
            return []
        out = []
        for u in d.get("result", []) or []:
            self.offset = max(self.offset, u.get("update_id", 0) + 1)
            cq = u.get("callback_query")
            if not cq:
                continue
            data = cq.get("data", "")
            if ":" in data:
                act, pid = data.split(":", 1)
                out.append(("approve" if act == "y" else "reject", pid, cq.get("id", "")))
        return out

    def ack(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
