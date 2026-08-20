"""Inline SVG icon set.

The UI uses no emoji anywhere: emoji render in the platform's own colours and
at the platform's own weight, which fights the monochrome terminal theme and
looks different on every machine. These are stroke-based glyphs that inherit
`currentColor`, so they take the colour of whatever they sit in.
"""

_SVG = (
    '<svg class="icon {cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true">{body}</svg>'
)

_PATHS = {
    # Open padlock -- the app's core action.
    "unlock": '<rect x="3" y="11" width="18" height="10" rx="2"/>'
              '<path d="M7 11V7a5 5 0 0 1 9.9-1"/><path d="M12 15v2"/>',
    "lock": '<rect x="3" y="11" width="18" height="10" rx="2"/>'
            '<path d="M7 11V7a5 5 0 0 1 10 0v4"/><path d="M12 15v2"/>',
    "download": '<path d="M12 3v12"/><path d="m7 12 5 5 5-5"/><path d="M4 21h16"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "folder-in": '<path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'
                 '<path d="M12 10v6m0 0-2.5-2.5M12 16l2.5-2.5"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 4v4h-4"/>'
               '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 20v-4h4"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 10v5"/><path d="M12 18h.01"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "skull": '<path d="M12 2c-5 0-9 3.6-9 8.2 0 2.6 1.3 4.5 3 5.8v2.6c0 .8.6 1.4 1.4 1.4h9.2c.8 0 '
             '1.4-.6 1.4-1.4V16c1.7-1.3 3-3.2 3-5.8C21 5.6 17 2 12 2Z"/>'
             '<circle cx="8.6" cy="10.4" r="1.9"/><circle cx="15.4" cy="10.4" r="1.9"/>'
             '<path d="M10.5 20v-3M13.5 20v-3"/>',
}


def icon(name: str, cls: str = "", stroke_width: float = 2) -> str:
    """Return an inline SVG for `name`, or an empty string if unknown."""
    body = _PATHS.get(name)
    if body is None:
        return ""
    return _SVG.format(cls=cls, sw=stroke_width, body=body)
