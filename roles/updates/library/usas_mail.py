#!/usr/bin/python
"""Sends one plain-text mail through an SMTP relay, with the relay's certificate checked.

community.general.mail opens TLS with Python's defaults, which don't check the server's certificate; this
module uses ssl.create_default_context() for both implicit TLS and STARTTLS, so a relay must present a
certificate the control node trusts (the system's CAs, or SSL_CERT_FILE) for its host name. Only the standard
library; runs on the control node (delegate_to: localhost).
"""

import smtplib
import ssl
from email.message import EmailMessage

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: usas_mail
short_description: Send a plain-text mail through an SMTP relay with certificate verification
description:
  - Sends one message with smtplib. With tls starttls or tls, the relay's certificate and host name are checked
    against the CAs the control node trusts. Headers are built by email.message, which refuses line breaks.
options:
  host: {description: The relay's host name or address., type: str, required: true}
  port: {description: The relay's port., type: int, default: 587}
  tls:
    description: starttls (upgrade, required), tls (implicit TLS, usually port 465) or off (plain, only on request).
    type: str
    choices: [starttls, tls, "off"]
    default: starttls
  username: {description: The user to sign in as; no sign-in when empty., type: str, default: ""}
  password: {description: The password to sign in with., type: str, default: "", no_log: true}
  sender: {description: The From address., type: str, required: true}
  to: {description: The recipients., type: list, elements: str, required: true}
  subject: {description: The subject line., type: str, required: true}
  body: {description: The plain-text message., type: str, required: true}
  timeout: {description: Seconds for the connection and each command., type: int, default: 30}
author: HoneyBearTech
"""

RETURN = r"""
recipients:
  description: The recipients the relay accepted.
  returned: success
  type: list
"""


def build_message(sender, to, subject, body):
    """The message; email.message raises ValueError for a header with a line break."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message


def send(params, smtp_class=smtplib.SMTP, smtp_ssl_class=smtplib.SMTP_SSL):
    """Sends the message; returns the accepted recipients. Raises smtplib, ssl or OS errors."""
    context = ssl.create_default_context()
    message = build_message(params["sender"], params["to"], params["subject"], params["body"])
    if params["tls"] == "tls":
        smtp = smtp_ssl_class(params["host"], params["port"], timeout=params["timeout"], context=context)
    else:
        smtp = smtp_class(params["host"], params["port"], timeout=params["timeout"])
    with smtp:
        smtp.ehlo()
        if params["tls"] == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if params["username"]:
            smtp.login(params["username"], params["password"])
        refused = smtp.send_message(message)
    return [r for r in params["to"] if r not in refused]


def main():
    module = AnsibleModule(
        argument_spec={
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "default": 587},
            "tls": {"type": "str", "default": "starttls", "choices": ["starttls", "tls", "off"]},
            "username": {"type": "str", "default": ""},
            "password": {"type": "str", "default": "", "no_log": True},
            "sender": {"type": "str", "required": True},
            "to": {"type": "list", "elements": "str", "required": True},
            "subject": {"type": "str", "required": True},
            "body": {"type": "str", "required": True},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=True,
    )
    if module.check_mode:
        module.exit_json(changed=False, recipients=[])
    try:
        accepted = send(module.params)
    except (smtplib.SMTPException, ssl.SSLError, OSError, ValueError) as error:
        module.fail_json(msg=f"Couldn't send the mail through {module.params['host']}:{module.params['port']}: {error}")
    module.exit_json(changed=True, recipients=accepted)


if __name__ == "__main__":
    main()
