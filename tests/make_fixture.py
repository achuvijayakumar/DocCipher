"""Build a minimal, valid, editing-restricted .docx fixture for testing.

Produces a real OOXML package with a <w:documentProtection> element so the
cracker can be exercised without needing Microsoft Word.
"""

import sys
import zipfile
from pathlib import Path

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""

DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:permStart w:id="1" w:edGrp="everyone"/>
<w:p><w:r><w:t>This document is restricted for editing.</w:t></w:r></w:p>
<w:permEnd w:id="1"/>
<w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p>
</w:body>
</w:document>"""

PROTECTION = (
    '<w:documentProtection w:edit="readOnly" w:enforcement="1" '
    'w:cryptProviderType="rsaAES" w:cryptAlgorithmClass="hash" '
    'w:cryptAlgorithmType="typeAny" w:cryptAlgorithmSid="14" '
    'w:cryptSpinCount="100000" w:hash="Zk7pJ2s=" w:salt="q1W2e3R4="/>'
)

SETTINGS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:zoom w:percent="100"/>
{protection}<w:defaultTabStop w:val="720"/>
</w:settings>"""


def build(path: Path, protected: bool = True) -> Path:
    settings = SETTINGS_TEMPLATE.format(protection=PROTECTION if protected else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Entry order mirrors what Word writes: content types first.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", DOCUMENT)
        zf.writestr("word/_rels/document.xml.rels", DOC_RELS)
        zf.writestr("word/settings.xml", settings)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/locked.docx")
    build(out, protected="--unprotected" not in sys.argv)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
