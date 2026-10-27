#include <zephyr/kernel.h>

int main(void)
{
	printk("zmig hello on %s\n", CONFIG_BOARD);
	return 0;
}
