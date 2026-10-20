"""Compact version-L QR: UUID bytes in Base45, for the 96-dot D11s head."""
from uuid import UUID

import qrcode
from qrcode.util import MODE_ALPHA_NUM, QRData

BASE45 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'


def compact_payload(battery_id: str) -> str:
    raw = UUID(battery_id).bytes
    groups = []
    for i in range(0, 16, 2):
        value = int.from_bytes(raw[i:i + 2], 'big')
        groups.append(BASE45[value % 45] + BASE45[(value // 45) % 45] + BASE45[value // 2025])
    # L is the version discriminator. 25 alphanumeric characters fit QR v1-L.
    return 'L' + ''.join(groups)


def compact_qr(battery_id: str):
    code = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L,
                         box_size=3, border=4)
    code.add_data(QRData(compact_payload(battery_id), mode=MODE_ALPHA_NUM))
    code.make(fit=False)
    return code.make_image()
