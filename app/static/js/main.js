// Main JavaScript for global UI interactions
document.addEventListener('DOMContentLoaded', () => {
    console.log('Fuzztracks UI Initialized');
    
    // Flash message auto-dismiss
    const flashMessages = document.querySelectorAll('.flash-message');
    if (flashMessages.length > 0) {
        setTimeout(() => {
            flashMessages.forEach(msg => {
                msg.style.opacity = '0';
                setTimeout(() => msg.remove(), 500);
            });
        }, 5000);
    }
});
