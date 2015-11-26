from django.template import RequestContext
from django.shortcuts import render


#Source: http://lincolnloop.com/blog/2008/may/10/getting-requestcontext-your-templates/
def render_to(template_name, title = ""):
    def renderer(func):
        def wrapper(request, *args, **kw):
            output = func(request, *args, **kw)
            if not isinstance(output, dict): return output
            return render(request, template_name, output)
        return wrapper
    return renderer
