"""Conservative shared unit allowance; no monetary or input-token guarantee."""
import threading


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('budget units must be nonnegative integers')
    return value


class ExperimentBudget:
    """Atomically reserve worst-case attempts and output tokens; reservations never refund."""
    def __init__(self, max_attempts, max_output_tokens):
        self._max_attempts = count(max_attempts)
        self._max_output_tokens = count(max_output_tokens)
        self._attempts = self._output = 0
        self._lock = threading.Lock()

    @property
    def reserved_attempts(self):
        with self._lock:
            return self._attempts

    @property
    def reserved_output_tokens(self):
        with self._lock:
            return self._output

    def reserve(self, attempts, output_tokens_per_attempt):
        attempts, tokens = count(attempts), count(output_tokens_per_attempt)
        with self._lock:
            if (self._attempts + attempts > self._max_attempts
                    or self._output + attempts * tokens > self._max_output_tokens):
                return False
            self._attempts += attempts
            self._output += attempts * tokens
            return True
