"""SMTP only: delivery links are never returned by the API or written to logs."""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def configuration():
    origin = os.environ.get('PUBLIC_URL', '').rstrip('/')
    parsed = urlsplit(origin)
    mode = os.environ.get('APP_ENV', 'production')
    tls = os.environ.get('SMTP_TLS', 'starttls')
    host = os.environ.get('SMTP_HOST', '')
    sender = os.environ.get('SMTP_FROM', '')
    port = int(os.environ.get('SMTP_PORT', '465' if tls == 'ssl' else '587'))
    if not 1 <= port <= 65535:
        raise ValueError('Port SMTP invalide')
    if (not host or not sender or any(c in sender for c in '\r\n') or
        parsed.scheme not in ('http', 'https') or not parsed.hostname or
        parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or
        tls not in ('starttls', 'ssl', 'none')):
        raise ValueError('Configuration du courrier incomplète')
    if mode not in ('local', 'test') and (parsed.scheme != 'https' or tls == 'none' or
                                         os.environ.get('COOKIE_SECURE', 'false').lower() != 'true'):
        raise ValueError('HTTPS, cookies sécurisés et SMTP chiffré requis en production')
    return origin, host, sender, tls


def available():
    try:
        configuration()
        return True
    except ValueError:
        return False


def deliver(recipient, purpose, token):
    try:
        origin, host, sender, tls = configuration()
        path, title = {'verify': ('verify', 'Vérifier votre adresse'),
                       'reset': ('reset', 'Réinitialiser votre mot de passe'),
                       'transfer': ('transfer', 'Proposition de transfert de batterie')}[purpose]
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = sender, recipient, 'LipoWatcher — ' + title
        message.set_content(f'{title}\n\n{origin}/{path}#token={token}\n\n'
                            'Lien temporaire, à usage unique. Ne le transmettez pas. '
                            'Ouvrir une proposition ne vaut pas acceptation. '
                            'Si vous n’avez pas demandé cette action, ignorez ce message.')
        port = int(os.environ.get('SMTP_PORT', '465' if tls == 'ssl' else '587'))
        factory = smtplib.SMTP_SSL if tls == 'ssl' else smtplib.SMTP
        options = {'context': ssl.create_default_context()} if tls == 'ssl' else {}
        with factory(host, port, timeout=10, **options) as smtp:
            if tls == 'starttls':
                smtp.starttls(context=ssl.create_default_context())
            username = os.environ.get('SMTP_USER', '')
            if username:
                smtp.login(username, os.environ.get('SMTP_PASSWORD', ''))
            smtp.send_message(message)
    except Exception:
        # Do not log exceptions, recipients, credentials, links or tokens.
        logger.warning('Envoi SMTP échoué ; vérifier la configuration du service de courrier.')
