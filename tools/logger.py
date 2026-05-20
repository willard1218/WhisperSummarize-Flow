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
    # Remove existing handlers to avoid duplicates
    for h in root.handlers[:]: root.removeHandler(h)
    for h in handlers: root.addHandler(h)

class AIConsumerLogger:
    """A wrapper for logging.Logger that supports custom keyword arguments like task and action."""
    def __init__(self, logger_instance, extra=None):
        self.logger = logger_instance
        self.extra = extra or {}

    def _log(self, level, msg, *args, **kwargs):
        # 1. Prepare 'extra' dict
        extra = self.extra.copy()
        if "extra" in kwargs:
            extra.update(kwargs.pop("extra"))
            
        # 2. Extract special keywords
        for key in ["task", "action", "model", "status", "duration"]:
            if key in kwargs:
                extra[key] = kwargs.pop(key)
        
        # 3. Call the underlying logger with increased stacklevel
        # Default stacklevel is 1. Since we have a wrapper (_log) and 
        # convenience methods (info, etc.), we need stacklevel=3 
        # to reach the real caller.
        kwargs.setdefault("stacklevel", 3)
        self.logger.log(level, msg, *args, extra=extra, **kwargs)

    def debug(self, msg, *args, **kwargs): self._log(logging.DEBUG, msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs): self._log(logging.INFO, msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs): self._log(logging.WARNING, msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs): self._log(logging.ERROR, msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs): self._log(logging.CRITICAL, msg, *args, **kwargs)
    def exception(self, msg, *args, **kwargs):
        kwargs["exc_info"] = True
        # For exception(), we also need stacklevel=3
        self._log(logging.ERROR, msg, *args, **kwargs)

    def setLevel(self, level): self.logger.setLevel(level)
    def isEnabledFor(self, level): return self.logger.isEnabledFor(level)

def get_logger(name):
    return AIConsumerLogger(logging.getLogger(name))

class TaskLogger(AIConsumerLogger):
    def __init__(self, name, task_label=None):
        super().__init__(logging.getLogger(name), {"task": task_label})
