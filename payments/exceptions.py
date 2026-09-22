class RobokassaError(Exception):
    """Base error for Robokassa test payments."""


class PaymentStartBlocked(RobokassaError):
    """Start is not allowed; message is safe to show a superuser."""


class CallbackRejected(RobokassaError):
    """Result/Success payload cannot be accepted. Never log secrets."""

    def __init__(self, code, public_message='bad request'):
        self.code = code
        self.public_message = public_message
        super().__init__(code)
