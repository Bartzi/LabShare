from django import template
register = template.Library()


@register.inclusion_tag('process_list.html')
def render_process_list(gpu_id, processes):
	return {
		"processes": processes,
		"gpu_id": gpu_id
	}