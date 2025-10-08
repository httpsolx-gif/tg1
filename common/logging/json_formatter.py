import json, time, logging

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        extra = {}
        for k, v in record.__dict__.items():
            if k not in ("args","msg","levelno","levelname","name","pathname","filename","module","exc_info","exc_text","stack_info","lineno","funcName","created","msecs","relativeCreated","thread","threadName","processName","process"):
                extra[k] = v
        if extra:
            base["extra"] = extra
        return json.dumps(base, ensure_ascii=False)
