#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/can.h>
#include <version.h>

#define GPIO_NODE DT_PATH(gpio_800)
#define CAN_NODE  DT_NODELABEL(can0)

int main(void)
{
	const struct device *can = DEVICE_DT_GET_OR_NULL(CAN_NODE);

	printk("zmig fixture on %s, kernel %s\n", CONFIG_BOARD, KERNEL_VERSION_STRING);
#if DT_NODE_EXISTS(GPIO_NODE)
	printk("emulated gpio at %s\n", DT_NODE_PATH(GPIO_NODE));
#endif
	printk("can0 sample point %d\n", DT_PROP_OR(CAN_NODE, sample_point, 0));
	if (can != NULL) {
		(void)can_recover(can, K_MSEC(100));
	}
	return 0;
}
