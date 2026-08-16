"""
Alert rules: evaluate recorded events against configured thresholds and notify.

Evaluation is periodic rather than inline. The budget alerting this replaces ran
inside the after-model callback and paid a DB query plus a blocking HTTP POST on
every successful response; error and guardrail conditions are window queries that
would be recomputed per request for no benefit. One scheduled pass reads two
tables instead.

The cooldown lives in `alert_rules.last_fired_at` and is claimed with a
conditional UPDATE, so it survives a restart and two processes cannot both
deliver the same alert — the previous implementation used a process-local dict
that did neither.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .database_client import get_database_client
from .models import AgentConfig, AlertRule, RateLimitConfig
from .notify import post_json, send_email

logger = logging.getLogger(__name__)

CONDITION_TYPES = ('agent_error_count', 'budget_threshold', 'guardrail_count')
DESTINATION_TYPES = ('http', 'email')
SCOPES = ('user', 'agent', 'project', 'global')

# Budget periods and the granularity at which "already alerted" resets.
_PERIOD_WINDOWS = {
    'hour': timedelta(hours=1),
    'day': timedelta(days=1),
    'month': timedelta(days=30),
}
_PERIOD_KEY_FORMATS = {
    'hour': '%Y-%m-%dT%H',
    'day': '%Y-%m-%d',
    'month': '%Y-%m',
}


def alerts_enabled() -> bool:
    return os.getenv("ALERTS_ENABLED", "false").lower() == "true"


class AlertService:
    """CRUD plus evaluation for alert rules."""

    def __init__(self):
        self.db_client = get_database_client()

    def _get_session(self):
        if not self.db_client or not self.db_client.is_connected():
            return None
        return self.db_client.get_session()

    # ------------------------------------------------------------------ #
    # CRUD                                                                #
    # ------------------------------------------------------------------ #

    def get_rules(self, scope: Optional[str] = None, condition_type: Optional[str] = None,
                  enabled_only: bool = False) -> List[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(AlertRule)
            if scope:
                query = query.filter(AlertRule.scope == scope)
            if condition_type:
                query = query.filter(AlertRule.condition_type == condition_type)
            if enabled_only:
                query = query.filter(AlertRule.is_enabled.is_(True))
            return [r.to_dict() for r in query.order_by(AlertRule.id.desc()).all()]
        except Exception as e:
            logger.error("Failed to list alert rules: %s", e)
            return []
        finally:
            session.close()

    def get_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return None
        try:
            rule = session.query(AlertRule).filter(AlertRule.id == rule_id).first()
            return rule.to_dict() if rule else None
        except Exception as e:
            logger.error("Failed to get alert rule %s: %s", rule_id, e)
            return None
        finally:
            session.close()

    def create_rule(self, **kwargs) -> Optional[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return None
        try:
            condition_config = kwargs.pop('condition_config', None)
            destination_config = kwargs.pop('destination_config', None)
            rule = AlertRule(**kwargs)
            rule.set_condition_config(condition_config or {})
            rule.set_destination_config(destination_config or {})
            session.add(rule)
            session.commit()
            session.refresh(rule)
            return rule.to_dict()
        except Exception as e:
            session.rollback()
            logger.error("Failed to create alert rule: %s", e)
            return None
        finally:
            session.close()

    def update_rule(self, rule_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return None
        try:
            rule = session.query(AlertRule).filter(AlertRule.id == rule_id).first()
            if not rule:
                return None
            condition_config = kwargs.pop('condition_config', None)
            destination_config = kwargs.pop('destination_config', None)
            for key, value in kwargs.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)
            if condition_config is not None:
                rule.set_condition_config(condition_config)
            if destination_config is not None:
                rule.set_destination_config(destination_config)
            session.commit()
            session.refresh(rule)
            return rule.to_dict()
        except Exception as e:
            session.rollback()
            logger.error("Failed to update alert rule %s: %s", rule_id, e)
            return None
        finally:
            session.close()

    def delete_rule(self, rule_id: int) -> bool:
        session = self._get_session()
        if not session:
            return False
        try:
            rule = session.query(AlertRule).filter(AlertRule.id == rule_id).first()
            if not rule:
                return False
            session.delete(rule)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error("Failed to delete alert rule %s: %s", rule_id, e)
            return False
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Scope resolution                                                     #
    # ------------------------------------------------------------------ #

    def _agent_names_for_scope(self, session, rule: AlertRule) -> Optional[List[str]]:
        """Agent names a rule covers, or None when it is not agent-restricted.

        Neither token_usage_logs nor guardrail_logs carries project_id, so a
        project-scoped rule has to be expanded into its agents.
        """
        if rule.scope == 'agent':
            return [rule.scope_id]
        if rule.scope == 'project':
            try:
                project_id = int(rule.scope_id)
            except (TypeError, ValueError):
                return []
            return [r[0] for r in session.query(AgentConfig.name).filter(
                AgentConfig.project_id == project_id).all()]
        return None

    # ------------------------------------------------------------------ #
    # Conditions                                                           #
    # ------------------------------------------------------------------ #

    def _measure(self, session, rule: AlertRule) -> Optional[Dict[str, Any]]:
        """Return {'value', 'threshold', 'detail'} or None when not measurable."""
        config = rule.get_condition_config()
        if rule.condition_type == 'agent_error_count':
            return self._measure_error_count(rule, config)
        if rule.condition_type == 'guardrail_count':
            return self._measure_guardrail_count(session, rule, config)
        if rule.condition_type == 'budget_threshold':
            return self._measure_budget(session, rule, config)
        logger.warning("Unknown condition_type '%s' on rule %s", rule.condition_type, rule.id)
        return None

    def _measure_error_count(self, rule: AlertRule, config: dict) -> Optional[Dict[str, Any]]:
        from .token_usage_service import get_token_usage_service

        window_minutes = int(config.get('window_minutes', 15))
        threshold = int(config.get('threshold', 5))
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        kwargs: Dict[str, Any] = {}
        if rule.scope == 'agent':
            kwargs['agent_name'] = rule.scope_id
        elif rule.scope == 'user':
            kwargs['user_id'] = rule.scope_id
        elif rule.scope == 'project':
            try:
                kwargs['project_id'] = int(rule.scope_id)
            except (TypeError, ValueError):
                return None
        value = get_token_usage_service().get_error_count_since(since, **kwargs)
        return {'value': value, 'threshold': threshold,
                'detail': {'window_minutes': window_minutes}}

    def _measure_guardrail_count(self, session, rule: AlertRule,
                                 config: dict) -> Optional[Dict[str, Any]]:
        from .guardrail_log_service import get_guardrail_log_service

        window_minutes = int(config.get('window_minutes', 60))
        threshold = int(config.get('threshold', 10))
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        value = get_guardrail_log_service().count_triggers_since(
            since=since,
            agent_names=self._agent_names_for_scope(session, rule),
            guardrail_type=config.get('guardrail_type') or None,
            action_taken=config.get('action_taken') or None,
        )
        return {'value': value, 'threshold': threshold,
                'detail': {'window_minutes': window_minutes}}

    def _measure_budget(self, session, rule: AlertRule, config: dict) -> Optional[Dict[str, Any]]:
        from .token_usage_service import get_token_usage_service

        period = config.get('period', 'day')
        if period not in _PERIOD_WINDOWS:
            return None
        threshold_pct = int(config.get('threshold_pct', 90))
        limit = config.get('token_limit')
        if not limit:
            limit = self._limit_from_rate_limit_config(session, rule, period)
        if not limit:
            # A budget rule with no limit anywhere is inert; the API warns at create time
            return None

        since = datetime.now(timezone.utc) - _PERIOD_WINDOWS[period]
        service = get_token_usage_service()
        if rule.scope == 'user':
            used = service.get_user_tokens_since(rule.scope_id, since)
        elif rule.scope == 'agent':
            used = service.get_agent_tokens_since(rule.scope_id, since)
        elif rule.scope == 'project':
            try:
                used = service.get_project_tokens_since(int(rule.scope_id), since)
            except (TypeError, ValueError):
                return None
        else:
            return None

        pct = int(100 * used / limit) if limit else 0
        return {'value': pct, 'threshold': threshold_pct,
                'detail': {'period': period, 'used': used, 'limit': limit}}

    def _limit_from_rate_limit_config(self, session, rule: AlertRule,
                                      period: str) -> Optional[int]:
        """Fall back to the budget already configured under Rate Limits."""
        column = {'hour': RateLimitConfig.tokens_per_hour,
                  'day': RateLimitConfig.tokens_per_day,
                  'month': RateLimitConfig.tokens_per_month}.get(period)
        if column is None:
            return None
        try:
            row = session.query(column).filter(
                RateLimitConfig.scope == rule.scope,
                RateLimitConfig.scope_id == rule.scope_id
            ).first()
            return int(row[0]) if row and row[0] else None
        except Exception as e:
            logger.error("Failed to resolve budget limit for rule %s: %s", rule.id, e)
            return None

    # ------------------------------------------------------------------ #
    # Firing                                                               #
    # ------------------------------------------------------------------ #

    def _period_key(self, period: str) -> str:
        return datetime.now(timezone.utc).strftime(_PERIOD_KEY_FORMATS.get(period, '%Y-%m-%d'))

    def _in_cooldown(self, rule: AlertRule) -> bool:
        if not rule.last_fired_at:
            return False
        last = rule.last_fired_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last < timedelta(seconds=rule.cooldown_seconds or 0)

    def _claim(self, session, rule: AlertRule) -> bool:
        """Take the right to deliver this alert. Returns False if someone else did.

        A conditional UPDATE is both the durable cooldown and the cross-process
        lock: exactly one caller can move last_fired_at out of the cooldown window.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=rule.cooldown_seconds or 0)
        try:
            updated = session.query(AlertRule).filter(
                AlertRule.id == rule.id,
                (AlertRule.last_fired_at.is_(None)) | (AlertRule.last_fired_at <= cutoff)
            ).update({
                AlertRule.last_fired_at: now,
                AlertRule.fire_count: AlertRule.fire_count + 1,
            }, synchronize_session=False)
            session.commit()
            return updated == 1
        except Exception as e:
            session.rollback()
            logger.error("Failed to claim alert rule %s: %s", rule.id, e)
            return False

    def _build_payload(self, rule: AlertRule, measurement: Dict[str, Any]) -> Dict[str, Any]:
        detail = measurement.get('detail') or {}
        if rule.condition_type == 'budget_threshold':
            # Keep the historical event name so webhooks written against the old
            # rate-limit alert keep working after the migration.
            event = 'rate_limit_alert'
            message = (f"{rule.scope} {rule.scope_id} has used {measurement['value']}% of its "
                       f"{detail.get('period')} token budget "
                       f"({detail.get('used')}/{detail.get('limit')})")
        elif rule.condition_type == 'agent_error_count':
            event = 'agent_error_alert'
            message = (f"{rule.scope} {rule.scope_id} recorded {measurement['value']} errors in "
                       f"the last {detail.get('window_minutes')} minutes")
        else:
            event = 'guardrail_alert'
            message = (f"{rule.scope} {rule.scope_id} triggered guardrails "
                       f"{measurement['value']} times in the last "
                       f"{detail.get('window_minutes')} minutes")
        return {
            'event': event,
            'rule_id': rule.id,
            'rule_name': rule.name,
            'condition_type': rule.condition_type,
            'scope': rule.scope,
            'scope_id': rule.scope_id,
            'value': measurement['value'],
            'threshold': measurement['threshold'],
            'message': message,
            'fired_at': datetime.now(timezone.utc).isoformat(),
            **detail,
        }

    def _deliver(self, rule: AlertRule, payload: Dict[str, Any]) -> tuple:
        config = rule.get_destination_config()
        if rule.destination_type == 'http':
            return post_json(config.get('url', ''), payload,
                             headers=config.get('headers') or {},
                             timeout=float(config.get('timeout', 30)))
        if rule.destination_type == 'email':
            subject = config.get('subject') or f"[MATE alert] {rule.name}"
            body = payload['message'] + "\n\n" + "\n".join(
                f"{k}: {v}" for k, v in payload.items() if k != 'message')
            return send_email(config.get('to', ''), subject, body)
        return False, f"unknown destination_type '{rule.destination_type}'"

    def evaluate_rule(self, rule_id: int, force: bool = False) -> Dict[str, Any]:
        """Evaluate one rule. force=True measures and delivers without touching cooldown.

        The dashboard test button uses force so a trial run cannot consume the
        rule's real cooldown or inflate fire_count.
        """
        session = self._get_session()
        if not session:
            return {'status': 'error', 'error': 'database unavailable'}
        try:
            rule = session.query(AlertRule).filter(AlertRule.id == rule_id).first()
            if not rule:
                return {'status': 'error', 'error': 'rule not found'}
            measurement = self._measure(session, rule)
            if measurement is None:
                return {'status': 'skipped', 'reason': 'not measurable',
                        'rule_id': rule_id}
            crossed = measurement['value'] >= measurement['threshold']
            result = {'status': 'ok', 'rule_id': rule_id, 'would_fire': crossed,
                      'value': measurement['value'], 'threshold': measurement['threshold']}
            if not force:
                return {**result, 'fired': False, 'reason': 'evaluate only'}

            payload = self._build_payload(rule, measurement)
            payload['test'] = True
            ok, detail = self._deliver(rule, payload)
            return {**result, 'delivery': {'ok': ok, 'detail': detail}}
        except Exception as e:
            logger.error("Failed to evaluate alert rule %s: %s", rule_id, e)
            return {'status': 'error', 'error': str(e)}
        finally:
            session.close()

    def evaluate_all(self) -> List[Dict[str, Any]]:
        """Evaluate every enabled rule. Never raises — this runs on the scheduler."""
        session = self._get_session()
        if not session:
            return []
        fired: List[Dict[str, Any]] = []
        try:
            rules = session.query(AlertRule).filter(AlertRule.is_enabled.is_(True)).all()
            for rule in rules:
                try:
                    # Cooldown first: a broken agent must not cost one aggregation
                    # query per evaluation pass while it is already alerting.
                    if self._in_cooldown(rule):
                        continue
                    measurement = self._measure(session, rule)
                    if measurement is None or measurement['value'] < measurement['threshold']:
                        continue
                    if not self._fire(session, rule, measurement):
                        continue
                    fired.append({'rule_id': rule.id, 'rule_name': rule.name,
                                  'value': measurement['value']})
                except Exception as e:
                    # One broken rule must not stop the rest of the pass
                    logger.error("Alert rule %s failed to evaluate: %s", rule.id, e)
            return fired
        except Exception as e:
            logger.error("Alert evaluation pass failed: %s", e)
            return fired
        finally:
            session.close()

    def _fire(self, session, rule: AlertRule, measurement: Dict[str, Any]) -> bool:
        """Claim, deliver, record. Returns True when this process delivered."""
        state = rule.get_last_state()
        period = (measurement.get('detail') or {}).get('period')
        if rule.condition_type == 'budget_threshold' and period:
            # Thresholds are per period, not per cooldown: crossing 90% today must
            # alert again tomorrow, and must not re-alert every cooldown until then.
            period_key = self._period_key(period)
            if state.get('period_key') != period_key:
                state = {'period_key': period_key, 'fired_thresholds': []}
            if measurement['threshold'] in state.get('fired_thresholds', []):
                return False

        if not self._claim(session, rule):
            return False

        payload = self._build_payload(rule, measurement)
        ok, detail = self._deliver(rule, payload)

        if rule.condition_type == 'budget_threshold' and period:
            state.setdefault('fired_thresholds', []).append(measurement['threshold'])
        state['last_value'] = measurement['value']
        try:
            rule.set_last_state(state)
            rule.last_error = None if ok else detail[:1000]
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to record alert result for rule %s: %s", rule.id, e)
        return ok


_alert_service: Optional[AlertService] = None


def get_alert_service() -> AlertService:
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
