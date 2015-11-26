from django import template

register = template.Library()

def extract_string(value):
  if not (value and value[0] == value[-1] and value[0] in ('"', "'")):
    return value
  return value[1:-1]

class IconNode(template.Node):
  def __init__(self, icon_name):
    self.icon_name = icon_name

  def render(self, context):
    return "<span class=\"glyphicon glyphicon-{}\"></span> ".format(self.icon_name)

def no_icon_error():
  raise template.TemplateSyntaxError("please choose one of the icons listed here: http://getbootstrap.com/components/#glyphicons!")


@register.tag
def icon(parser, token):
  split = token.contents.split(' ')
  if len(split) < 2: return no_icon_error()
  if len(split) > 2: raise TemplateSyntaxError("too many arguments!")
  icon_name = extract_string(token.contents.split(' ')[1])
  if not icon_name:
    return no_icon_error()
  return IconNode(icon_name)
