import queue
import threading
import time
from src.llm.sentence import Sentence
from src import utils

logger = utils.get_logger()


class SentenceQueue:
    __logging_level = 42
    __should_log = False

    def __init__(self) -> None:
        self.__queue: queue.Queue[Sentence] = queue.Queue[Sentence]()
        self.__get_lock: threading.Lock = threading.Lock()
        self.__put_lock: threading.Lock = threading.Lock()
        self.__more_lock: threading.Lock = threading.Lock()
        self.__is_more_to_come: bool = False
        self.__cancelled: threading.Event = threading.Event()

    @property
    def is_more_to_come(self) -> bool:
        with self.__more_lock:
            return self.__is_more_to_come

    @is_more_to_come.setter
    def is_more_to_come(self, value: bool):
        with self.__more_lock:
            self.__is_more_to_come = value

    @utils.time_it
    def get_next_sentence(self) -> Sentence | None:
        self.log(f"Trying to aquire get_lock to get next sentence")
        with self.__get_lock:
            if self.__queue.qsize() > 0 or self.is_more_to_come:
                # Use short timeout loops so clear() can interrupt us quickly
                # by setting __cancelled and __is_more_to_come = False
                deadline = time.time() + 30
                while time.time() < deadline:
                    if self.__cancelled.is_set():
                        self.__cancelled.clear()
                        self.log("get_next_sentence cancelled by clear()")
                        return None
                    try:
                        retrieved_sentence = self.__queue.get(timeout=0.5)
                        self.log(f"Retrieved '{retrieved_sentence.text}'")
                        return retrieved_sentence
                    except queue.Empty:
                        # Re-check if we should still be waiting
                        if not self.is_more_to_come and self.__queue.qsize() == 0:
                            self.log("No more to come and queue empty, returning None")
                            return None
                        continue
                logger.warning("Sentence queue timeout: waited 30s but no sentence arrived")
                return None
            else:
                self.log(f"Nothing to get from queue, returning None")
                return None
    
    @utils.time_it
    def put(self, new_sentence: Sentence):
        self.log(f"Trying to aquire put_lock to put '{new_sentence.text}'")
        
        # DIAGNOSTIC: Warn if sentence queued without TTS
        if new_sentence.text.strip() and not new_sentence.voice_file and new_sentence.duration == 0:
            import traceback
            logger.warning(f"⚠️  SENTENCE QUEUED WITHOUT TTS: '{new_sentence.text[:80]}'")
            logger.warning(f"Stack trace:\n{''.join(traceback.format_stack()[-5:])}")  # Last 5 frames
        
        with self.__put_lock:
            self.log(f"Putting '{new_sentence.text}'")
            self.__queue.put(new_sentence)

    @utils.time_it
    def put_at_front(self, new_sentence: Sentence):
        self.log(f"Trying to aquire get_lock to put_at_front '{new_sentence.text}'")
        with self.__get_lock:
            self.log(f"Trying to aquire put_lock to put_at_front '{new_sentence.text}'")
            with self.__put_lock:            
                sentence_list: list[Sentence] = []
                try:
                    while True:
                        sentence_list.append(self.__queue.get_nowait() )
                except queue.Empty:
                    pass
                self.__queue.put_nowait(new_sentence)
                for s in sentence_list:
                    self.__queue.put_nowait(s)

    @utils.time_it
    def clear(self):
        self.log(f"Clearing queue")
        # Signal any blocking get_next_sentence() to exit quickly
        with self.__more_lock:
            self.__is_more_to_come = False
        self.__cancelled.set()
        # Drain the queue (Queue is thread-safe, no lock needed for get_nowait)
        try:
            while True:
                self.__queue.get_nowait()
        except queue.Empty:
            pass
        # Clear the cancel flag so subsequent get_next_sentence calls work normally
        # (e.g. initiate_end_sequence puts a goodbye sentence AFTER calling clear)
        self.__cancelled.clear()
    
    @utils.time_it
    def log(self, text: str):
        if(self.__should_log):
            logger.log(self.__logging_level, text)
