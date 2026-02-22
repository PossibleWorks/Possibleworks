app_name = "possibleworks"
app_title = "Possibleworks"
app_publisher = "Possibleworks"
app_description = "White-label branding app for Frappe/ERPNext"
app_email = "contact@possibleworks.com"
app_license = "MIT"
app_logo_url = "/assets/possibleworks/images/possibleworks-logo.svg"


# Template resolution: our app first so overrides apply
template_apps = ["possibleworks", "erpnext", "hrms", "frappe"]

# Desk assets: CSS (cacheable) + JS patches
app_include_css = ["/assets/possibleworks/css/whitelabel.css"]
app_include_js = ["/assets/possibleworks/js/whitelabel.js"]

after_install = "possibleworks.setup.after_install.set_default_branding"

website_context = {
	"favicon": "/assets/possibleworks/images/possibleworks-logo.svg",
	"splash_image": "/assets/possibleworks/images/possibleworks-logo.svg"
}
