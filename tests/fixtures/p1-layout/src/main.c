#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>

int main(void)
{
	static const char value[] = "fixture";

	settings_subsys_init();
	settings_save_one("zmig/value", value, sizeof(value));
	printk("p1-layout on %s\n", CONFIG_BOARD_TARGET);
	return 0;
}
