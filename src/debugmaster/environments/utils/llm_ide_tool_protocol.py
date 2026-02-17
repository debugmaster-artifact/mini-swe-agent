import json


class CodeContext(object):
    def __init__(self, file_path, line_number):
        self.file_path = file_path
        self.line_number = line_number

    def to_dict(self):
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
        }


class LLMIDEToolResponseFormat(object):
    @staticmethod
    def from_string(raw_str):
        if raw_str is None:
            return []
        if isinstance(raw_str, bytes):
            raw_str = raw_str.decode("utf-8", "replace")
        start_tag = "<tool-response>"
        end_tag = "</tool-response>"
        json_strs = []
        search_start = 0
        while True:
            start_idx = raw_str.find(start_tag, search_start)
            if start_idx == -1:
                break
            end_idx = raw_str.find(end_tag, start_idx + len(start_tag))
            if end_idx == -1:
                break
            json_strs.append(raw_str[start_idx + len(start_tag) : end_idx].strip())
            search_start = end_idx + len(end_tag)
        if not json_strs:
            json_strs = [raw_str]
        results = []
        for json_str in json_strs:
            try:
                data = json.loads(json_str)
            except Exception:
                continue
            code_contexts = None
            if data.get("code_context") is not None:
                code_contexts = [
                    CodeContext(ctx.get("file_path"), ctx.get("line_number"))
                    for ctx in data["code_context"]
                ]
            results.append(LLMIDEToolResponseFormat(
                package_name=data.get("package_name"),
                output=data.get("output"),
                returncode=data.get("returncode"),
                code_context=code_contexts,
                status=data.get("status"),
            ))
        return results

    @staticmethod
    def from_json(json_str):
        return LLMIDEToolResponseFormat.from_string(json_str)


    def __init__(self, package_name, output, returncode, code_context=None, status=None):
        self.package_name = package_name
        self.output = output
        self.returncode = returncode
        self.code_context = code_context
        self.status = status

    def to_dict(self):
        return {
            "package_name": self.package_name,
            "output": self.output,
            "returncode": self.returncode,
            "code_context": [ctx.to_dict() for ctx in self.code_context] if self.code_context else None,
            "status": self.status if self.status else None,
        }

    def to_json(self):
        return json.dumps(self.to_dict())

    def __str__(self):
        return "<tool-response>{}</tool-response>".format(self.to_json())
