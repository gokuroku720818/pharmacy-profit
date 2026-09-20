"""Conservative HTTP response protection for a financial SaaS.

No changes to authentication, financial data, or application caching. Only
client/proxy caching of dynamic pages and exports is disabled; versioned/static
assets keep the application's existing browser cache policy.
"""

from flask import request


def install(app):
    """Register security headers once, before Gunicorn accepts requests."""
    if app.extensions.get('response_security_installed'):
        return

    @app.after_request
    def protect_financial_responses(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if not request.path.startswith('/static/'):
            # Override cache headers set by send_file and other endpoints.
            # This covers login, private pages, redirects, errors, and exports.
            response.headers['Cache-Control'] = 'private, no-store, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    app.extensions['response_security_installed'] = True
