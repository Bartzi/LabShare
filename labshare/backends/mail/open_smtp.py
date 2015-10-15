import smtplib
import threading

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


class OpenSMTPBackend(SMTPEmailBackend):
    """
    A wrapper that manages the SMTP network connection.
    """
    def __init__(self, host=None, port=None, fail_silently=False, timeout=None, **kwargs):
        super(SMTPEmailBackend, self).__init__(host=host, port=port, fail_silently=fail_silently)
        self.host = host or settings.EMAIL_HOST
        self.port = port or settings.EMAIL_PORT
        self.timeout = settings.EMAIL_TIMEOUT if timeout is None else timeout
        self.connection = None
        self._lock = threading.RLock()

    def open(self):
        """
        Ensures we have a connection to the email server. Returns whether or
        not a new connection was required (True or False).
        """
        if self.connection:
            # Nothing to do if the connection is already open.
            return False

        connection_params = {}
        if self.timeout is not None:
            connection_params['timeout'] = self.timeout
        try:
            self.connection = smtplib.SMTP(self.host, self.port, **connection_params)
            self.connection.ehlo()

            return True
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise

    def close(self):
        """Closes the connection to the email server."""
        if self.connection is None:
            return
        try:
            try:
                self.connection.quit()
            except smtplib.SMTPServerDisconnected:
                self.connection.close()
            except smtplib.SMTPException:
                if self.fail_silently:
                    return
                raise
        finally:
            self.connection = None
