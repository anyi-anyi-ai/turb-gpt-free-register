# -*- coding: utf-8 -*-
"""PerimeterX Render-flow Triggers: Site-specific navigation to evoke the challenge gate."""

from .outlook import render_outlook_gate

RENDERERS = {
    "outlook_signup": render_outlook_gate,
}
