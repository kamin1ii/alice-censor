"""Entry point for the frozen build.

PyInstaller runs its entry script as `__main__` with no package context, so
the relative import in alice_censor/__main__.py cannot resolve and the exe
dies on launch with "attempted relative import with no known parent
package". Importing absolutely from here sidesteps that.

Running the app from source is unaffected and still goes through
`python -m alice_censor`.
"""

import sys

from alice_censor.gui.app import run

if __name__ == "__main__":
    sys.exit(run())
