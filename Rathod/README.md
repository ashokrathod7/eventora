# EVENTORA FINAL - Separate HTML/CSS

The working Flask shell is `templates/index.html`. Page markup is separated into `templates/pages/*.html`; common UI components are in `templates/components/*.html`; CSS is in `static/css/common.css` plus one CSS file per page. Static asset URLs use Flask `url_for`, so CSS loads correctly on local Flask and Railway.
