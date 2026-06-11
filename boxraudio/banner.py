"""
banner.py — ASCII art banner with tagline.
"""

from rich.console import Console
from rich.text import Text
from boxraudio import TAGLINE


BANNER_ASCII = r"""
   __________             __________ 
   \______   \ _______  __\______   \
    |    |  _//  _ \  \/  /|       _/
    |    |   (  <_> >    < |    |   \
    |______  /\____/__/\_ \|____|_  /
           \/            \/       \/
"""


def print_banner(console: Console = None):
    """Print the BoxR banner with tagline."""
    if console is None:
        console = Console()
    console.print()
    banner_text = Text(BANNER_ASCII, style="bold bright_cyan")
    console.print(banner_text)
    tagline = Text(f"  {TAGLINE}", style="dim italic")
    console.print(tagline)
    console.print()
