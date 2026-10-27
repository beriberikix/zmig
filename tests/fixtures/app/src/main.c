#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>

#define GPIO_NODE DT_PATH(gpio_800)
#define CAN_NODE  DT_NODELABEL(can0)

int main(void)
{
	printk("zmig fixture on %s\n", CONFIG_BOARD);
#if DT_NODE_EXISTS(GPIO_NODE)
	printk("emulated gpio at %s\n", DT_NODE_PATH(GPIO_NODE));
#endif
	printk("can0 sample point %d\n", DT_PROP(CAN_NODE, sample_point));
	return 0;
}
