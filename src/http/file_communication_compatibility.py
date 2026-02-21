import ast
import json
import os
from pathlib import Path
from threading import Thread
import time
from typing import Any
import requests

class file_communication_compatibility:
    """Every instance of this class monitors a single file and once certain JSON is written to it, it forwards this to Mantella's HTTP server

    Returns:
        _type_: _description_
    """
    COMMUNICATION_FILE_NAME: str = "_mantella_communication.txt"
    BASE_URL: str = "http://localhost:"
    KEY_ROUTE: str = "mantella_route"

    def __init__(self, path_to_file: str, port: int) -> None:
        self.__file: str = os.path.join(path_to_file, self.COMMUNICATION_FILE_NAME)
        self.__write_response("") #Create or clear file
        self.__url: str = self.BASE_URL + str(port) + "/"
        self.__monitor_thread = Thread(None, self.__monitor, None, []).start()

    def __monitor(self):
        reply: str = ""
        while True:
            try:
                json_text = self.__load_request_when_available(reply)
                json_request = json.loads(json_text)
                json_request = self.__lower_keys(json_request)
                if not json_request.__contains__(self.KEY_ROUTE):
                    continue
                route: str = json_request[self.KEY_ROUTE]
                route = route.lower()
                json_request[self.KEY_ROUTE] = route
                reply = self.__send_request_to_mantella(route, json_request)
                self.__write_response(reply)
            except json.JSONDecodeError as e:
                print(f'[file_communication] Malformed JSON in communication file, skipping: {e}')
                time.sleep(0.1)
            except Exception as e:
                print(f'[file_communication] Error in monitor loop: {type(e).__name__}: {e}')
                time.sleep(0.5)
    
    def __send_request_to_mantella(self, route: str, json_object: dict[str, Any]) -> str:
        url: str = self.__url + route
        header = {
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        response = requests.post(url=url, headers=header, json=json_object, timeout=120)
        response.raise_for_status()
        reply: Any = response.json()
        return json.dumps(reply)

    def __load_request_when_available(self, last_reply: str, timeout: float = 120) -> str:
        text = ""
        start = time.time()
        while text == '' or text == last_reply:
            with open(self.__file, 'r', encoding='utf-8') as f:
                text = f.read().strip()
            if time.time() - start > timeout:
                raise TimeoutError(f"No new data in communication file after {timeout}s")
            # decrease stress on CPU while waiting for file to populate
            time.sleep(0.01)
        return text
    
    def __write_response(self, response: str):
        max_attempts = 2
        delay_between_attempts = 5

        for attempt in range(max_attempts):
            try:
                with open(self.__file, 'w', encoding='utf-8') as f:
                    f.write(response)
                break
            except PermissionError:
                print(f'Permission denied to write to {self.__file}. Retrying...')
                if attempt + 1 == max_attempts:
                    raise
                else:
                    time.sleep(delay_between_attempts)
        return None
    
    def __lower_keys(self, json_object: Any) -> Any:
        if isinstance(json_object, list):
            return [self.__lower_keys(v) for v in json_object]
        elif isinstance(json_object, dict):
            return dict((k.lower(), self.__lower_keys(v)) for k, v in json_object.items())
        else:
            return json_object  