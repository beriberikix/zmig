#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/settings/settings.h>

int main(void)
{
	if (bt_enable(NULL) == 0) {
		settings_load();
	}
	return 0;
}
