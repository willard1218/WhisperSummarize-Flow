import logging
import json
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "task"): log_record["task"] = record.task
        if hasattr(record, "action"): log_record["action"] = record.action
        
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record, ensure_ascii=False)

class KVFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        parts = [
            f"time=\"{timestamp}\"",
            f"lvl=\"{record.levelname}\"",
            f"mod=\"{record.module}\"",
        ]
        if hasattr(record, "task"): parts.append(f"task=\"{record.task}\"")
        if hasattr(record, "action"): parts.append(f"action=\"{record.action}\"")
        
        msg = record.getMessage().replace('"', '\\"').replace('\n', '\\n')
        parts.append(f"msg=\"{msg}\"")
        if record.exc_info:
            parts.append(f"exc=\"{self.formatException(record.exc_info).replace('\n', ' ')}\"")
        return " ".join(parts)

def setup_logging(level=logging.INFO, format_type="kv", log_file=None, max_bytes=10*1024*1024, backup_count=5):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file: 
        handlers.append(RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"))

    if format_type == "json": formatter = JsonFormatter()
    elif format_type == "kv": formatter = KVFormatter()
    else: formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(module)s: %(message)s')
    
    for h in handlers: h.setFormatter(formatter)
    
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]: root.removeHandler(h)
    for h in handlers: root.addHandler(h)

class AIConsumerLogger(logging.LoggerAdapter):
    """Allows passing context like task and action as keywords."""
    def __init__(self, logger_instance, extra=None):
        super().__init__(logger_instance, extra or {})

    def process(self, msg, kwargs):
        # Move known keywords to 'extra'
        extra = kwargs.get("extra", {}).copy()
        for key in ["task", "action", "model", "status", "duration"]:
            if key in kwargs:
                extra[key] = kwargs.pop(key)
        kwargs["extra"] = extra
        return msg, kwargs

def get_logger(name):
    return AIConsumerLogger(logging.getLogger(name))

class TaskLogger(AIConsumerLogger):
    def __init__(self, name, task_label=None):
        super().__init__(logging.getLogger(name), {"task": task_label})
