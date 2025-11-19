// RACH INFRA GearOnRent - Main JavaScript
// ==========================================

(function () {
    const initAOS = () => {
        if (window.AOS) {
            window.AOS.init({
                duration: 800,
                easing: 'ease-in-out',
                once: true,
                offset: 100,
            });
        }
    };

    const initScrollEffects = () => {
        const header = document.getElementById('header');
        const scrollToTopBtn = document.getElementById('scrollToTop');

        const handleScroll = () => {
            if (header) {
                if (window.scrollY > 100) {
                    header.classList.add('scrolled');
                } else {
                    header.classList.remove('scrolled');
                }
            }

            if (scrollToTopBtn) {
                if (window.scrollY > 100) {
                    scrollToTopBtn.classList.add('visible');
                } else {
                    scrollToTopBtn.classList.remove('visible');
                }
            }
        };

        window.addEventListener('scroll', handleScroll);

        if (scrollToTopBtn) {
            scrollToTopBtn.addEventListener('click', () => {
                window.scrollTo({ top: 0, behavior: 'smooth' });
            });
        }
    };

    const initMobileMenu = () => {
        const mobileMenuToggle = document.getElementById('mobileMenuToggle');
        const navMenu = document.getElementById('navMenu');

        if (mobileMenuToggle && navMenu) {
            mobileMenuToggle.addEventListener('click', () => {
                navMenu.style.display = navMenu.style.display === 'flex' ? 'none' : 'flex';
                const icon = mobileMenuToggle.querySelector('i');
                if (icon) {
                    icon.classList.toggle('fa-bars');
                    icon.classList.toggle('fa-times');
                }
            });

            document.querySelectorAll('.nav-link').forEach((link) => {
                link.addEventListener('click', () => {
                    if (window.innerWidth <= 768) {
                        navMenu.style.display = 'none';
                        const icon = mobileMenuToggle.querySelector('i');
                        if (icon) {
                            icon.classList.add('fa-bars');
                            icon.classList.remove('fa-times');
                        }
                    }
                });
            });
        }
    };

    const initSmoothScroll = () => {
        document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
            anchor.addEventListener('click', (event) => {
                const target = document.querySelector(anchor.getAttribute('href'));
                if (target) {
                    event.preventDefault();
                    const headerOffset = 80;
                    const elementPosition = target.getBoundingClientRect().top;
                    const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

                    window.scrollTo({
                        top: offsetPosition,
                        behavior: 'smooth',
                    });
                }
            });
        });
    };

    const parseFloatSafe = (value) => {
        const parsed = parseFloat(value);
        return Number.isFinite(parsed) ? parsed : 0;
    };

    const formatCurrency = (value) => `₹${Math.round(value).toLocaleString('en-IN')}`;

    const renderCalculatorResult = ({ equipmentName, details, breakdown, totalCost, whatsappMessage, quoteParams }) => {
        const resultDiv = document.getElementById('calculatorResult');
        if (!resultDiv) {
            return;
        }

        const breakdownHTML = breakdown
            .map((item) => {
                const baseClass = 'breakdown-item';
                const classes = item.isTotal ? `${baseClass}` : baseClass;
                const styles = item.isTotal
                    ? 'font-size: 1.5rem; font-weight: 700; padding-top: 1.5rem; border-top: 2px solid rgba(255, 255, 255, 0.3);'
                    : '';
                return `\n                <div class="${classes}" style="${styles}">\n                    <span class="breakdown-label">${item.label}</span>\n                    <span class="breakdown-value">${item.value}</span>\n                </div>`;
            })
            .join('');

        const quoteURL = `/gear_on_rent/quote_request?${quoteParams.toString()}`;
        const whatsappURL = `https://wa.me/919289375999?text=${encodeURIComponent(whatsappMessage)}`;

        resultDiv.innerHTML = `
            <div class="result-content">
                <h3><i class="fas fa-file-invoice-dollar"></i> Cost Estimate</h3>
                <div style="background: rgba(255, 255, 255, 0.1); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                    <h4 style="margin-bottom: 0.5rem; font-size: 1.3rem;">${equipmentName}</h4>
                    <p style="color: rgba(255, 255, 255, 0.8); margin: 0;">${details}</p>
                </div>
                <div class="result-breakdown">${breakdownHTML}</div>
                <div class="result-actions">
                    <a href="${whatsappURL}" class="btn" target="_blank">
                        <i class="fab fa-whatsapp"></i> Get Official Quote on WhatsApp
                    </a>
                    <a href="#contact" class="btn">
                        <i class="fas fa-phone-alt"></i> Request Callback
                    </a>
                    <a href="${quoteURL}" class="btn" target="_blank">
                        <i class="fas fa-print"></i> Print Estimate
                    </a>
                </div>
            </div>
        `;
    };

    const setupCalculator = () => {
        const rentalTypeSelect = document.getElementById('rentalType');
        const equipmentTypeSelect = document.getElementById('equipmentType');
        const hourlyOptions = document.getElementById('hourlyOptions');
        const productionOptions = document.getElementById('productionOptions');

        if (!rentalTypeSelect || !equipmentTypeSelect || !hourlyOptions || !productionOptions) {
            return;
        }

        rentalTypeSelect.addEventListener('change', (event) => {
            const selectedType = event.target.value;
            if (selectedType === 'hourly') {
                hourlyOptions.style.display = 'block';
                productionOptions.style.display = 'none';
            } else if (selectedType === 'production') {
                hourlyOptions.style.display = 'none';
                productionOptions.style.display = 'block';
            } else {
                hourlyOptions.style.display = 'none';
                productionOptions.style.display = 'none';
            }
        });

        window.calculateRental = () => {
            const rentalType = rentalTypeSelect.value;
            const includeOperator = Boolean(document.getElementById('includeOperator')?.checked);
            const includeMaintenance = Boolean(document.getElementById('includeMaintenance')?.checked);
            const selectedOption = equipmentTypeSelect.options[equipmentTypeSelect.selectedIndex];

            if (!rentalType) {
                window.alert('Please select a rental type');
                return;
            }

            if (!selectedOption || !selectedOption.value) {
                window.alert('Please select an equipment type');
                return;
            }

            const equipmentName = selectedOption.text;
            const productId = selectedOption.value;
            let baseCost = 0;
            const breakdown = [];
            let details = '';
            const quoteParams = new URLSearchParams({
                product_id: productId,
                equipment: equipmentName,
                rental_type: rentalType,
            });

            if (rentalType === 'hourly') {
                const durationType = document.getElementById('durationType')?.value || 'hourly';
                const duration = parseFloatSafe(document.getElementById('duration')?.value);

                if (!duration || duration <= 0) {
                    window.alert('Please enter a valid duration');
                    return;
                }

                if (durationType === 'hourly') {
                    const hourlyRate = parseFloatSafe(selectedOption.dataset.hourly);
                    baseCost = hourlyRate * duration;
                    details = `${duration} hours × ₹${hourlyRate}/hour`;
                    quoteParams.set('duration_type', 'hourly');
                } else {
                    const dailyRate = parseFloatSafe(selectedOption.dataset.daily);
                    baseCost = dailyRate * duration;
                    details = `${duration} days × ₹${dailyRate}/day`;
                    quoteParams.set('duration_type', 'daily');
                }
                breakdown.push({ label: 'Equipment Rental', value: formatCurrency(baseCost) });
                quoteParams.set('duration', duration);

                if (includeOperator) {
                    const operatorRate = (quoteParams.get('duration_type') === 'daily') ? 3500 : 500;
                    const operatorCost = operatorRate * duration;
                    breakdown.push({ label: 'Operator Cost', value: formatCurrency(operatorCost) });
                    baseCost += operatorCost;
                }
            } else if (rentalType === 'production') {
                const productionVolume = parseFloatSafe(document.getElementById('productionVolume')?.value);
                const projectDuration = parseFloatSafe(document.getElementById('projectDuration')?.value);

                if (!productionVolume || productionVolume <= 0) {
                    window.alert('Please enter a valid production volume');
                    return;
                }

                if (!projectDuration || projectDuration <= 0) {
                    window.alert('Please enter a valid project duration');
                    return;
                }

                const productionRate = parseFloatSafe(selectedOption.dataset.production);
                baseCost = productionRate * productionVolume;
                details = `${productionVolume} m³ × ₹${productionRate}/m³ (${projectDuration} days project)`;
                breakdown.push({ label: 'Equipment Rental', value: formatCurrency(baseCost) });
                quoteParams.set('production_volume', productionVolume);
                quoteParams.set('project_duration', projectDuration);

                if (includeOperator) {
                    const operatorCost = 3500 * projectDuration;
                    breakdown.push({ label: 'Operator Cost', value: formatCurrency(operatorCost) });
                    baseCost += operatorCost;
                }
            }

            let maintenanceCost = 0;
            if (includeMaintenance) {
                maintenanceCost = baseCost * 0.10;
                breakdown.push({ label: 'Premium Maintenance', value: formatCurrency(maintenanceCost) });
                baseCost += maintenanceCost;
                quoteParams.set('include_maintenance', '1');
            }

            const gst = baseCost * 0.18;
            breakdown.push({ label: 'GST (18%)', value: formatCurrency(gst) });

            const totalCost = baseCost + gst;
            breakdown.push({ label: 'Total Cost', value: formatCurrency(totalCost), isTotal: true });

            if (includeOperator) {
                quoteParams.set('include_operator', '1');
            }
            quoteParams.set('amount', Math.max(Math.round(totalCost), 0));
            quoteParams.set('gst', gst.toFixed(2));
            if (details) {
                quoteParams.set('details', details);
            }

            const whatsappMessage = `Hi, I need a quote for ${equipmentName}. Estimated cost: ${formatCurrency(totalCost)}. ${details}`;

            renderCalculatorResult({
                equipmentName,
                details,
                breakdown,
                totalCost,
                whatsappMessage,
                quoteParams,
            });
        };
    };

    const downloadRateCard = () => {
        const rateCardContent = `
========================================
RACH INFRA GearOnRent - Rate Card 2025
========================================

Contact: +91 92893 75999
Email: gearonrent@rachinfra.com
Website: www.rachinfra.com

EXCAVATORS & LOADERS
-------------------
20T Hydraulic Excavator
- Hourly: ₹1,200/hr
- Daily: ₹8,500/day
- Production: ₹95/m³

Mini Excavator (5T)
- Hourly: ₹800/hr
- Daily: ₹5,000/day
- Production: ₹65/m³

Wheel Loader (3m³)
- Hourly: ₹1,000/hr
- Daily: ₹7,000/day
- Production: ₹85/m³

Backhoe Loader
- Hourly: ₹900/hr
- Daily: ₹6,000/day
- Production: ₹75/m³

CONCRETE EQUIPMENT
-----------------
Concrete Mixer (500L)
- Hourly: ₹600/hr
- Daily: ₹4,000/day
- Production: ₹80/m³

CRANES & LIFTING
---------------
Mobile Crane (25T)
- Hourly: ₹2,500/hr
- Daily: ₹18,000/day
- Production: ₹150/m³

Tower Crane (50T)
- Hourly: ₹3,500/hr
- Daily: ₹25,000/day
- Production: ₹200/m³

COMPACTION EQUIPMENT
-------------------
Road Roller (10T)
- Hourly: ₹700/hr
- Daily: ₹5,500/day
- Production: ₹70/m³

Plate Compactor
- Hourly: ₹500/hr
- Daily: ₹3,500/day
- Production: ₹60/m³

POWER EQUIPMENT
--------------
Generator (125 KVA)
- Hourly: ₹400/hr
- Daily: ₹3,000/day
- Production: ₹50/m³

Forklift (3T)
- Hourly: ₹600/hr
- Daily: ₹4,500/day
- Production: ₹65/m³

Bulldozer (D6)
- Hourly: ₹1,500/hr
- Daily: ₹10,000/day
- Production: ₹110/m³

ADDITIONAL SERVICES
------------------
- Operator: +₹500/hr or +₹3,500/day
- Premium Maintenance: +10% of rental cost
- GST: 18% (as per government rates)
- Delivery charges: As per location

TERMS & CONDITIONS
-----------------
✓ Minimum rental: 4 hours (hourly) / 30 days (production)
✓ Fuel cost: Client responsibility
✓ Maintenance included during rental
✓ IoT tracking on all equipment
✓ 24/7 emergency support available
✓ Same-day delivery in Delhi NCR
✓ Pan-India service (<72 hours)

PAYMENT TERMS
------------
- Advance: 30% booking amount
- Credit facility available for corporate clients
- Payment methods: Bank transfer, cheque, UPI

CANCELLATION POLICY
------------------
- 48 hours notice: Full refund
- 24 hours notice: 50% refund
- Less than 24 hours: No refund

For custom requirements or bulk orders,
please contact our team for special rates.

© 2025 RACH INFRA Pvt. Ltd.
CIN: U23949HR2025OPC127904
========================================
        `;

        const blob = new Blob([rateCardContent], { type: 'text/plain' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'RACH_INFRA_GearOnRent_RateCard_2025.txt';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);

        window.alert('Rate card downloaded successfully! Check your downloads folder.');
    };

    const setupRateCardDownload = () => {
        window.downloadRateCard = downloadRateCard;
    };

    const setupContactForm = () => {
        const contactForm = document.getElementById('contactForm');
        if (!contactForm) {
            return;
        }

        contactForm.addEventListener('submit', (event) => {
            event.preventDefault();

            const fullName = document.getElementById('fullName')?.value || '';
            const companyName = document.getElementById('companyName')?.value || '';
            const email = document.getElementById('email')?.value || '';
            const phone = document.getElementById('phone')?.value || '';
            const projectLocation = document.getElementById('projectLocation')?.value || '';
            const projectType = document.getElementById('projectType')?.value || '';
            const equipmentNeeded = document.getElementById('equipmentNeeded')?.value || '';
            const rentalModel = document.getElementById('rentalModel')?.value || '';
            const startDate = document.getElementById('startDate')?.value || '';
            const message = document.getElementById('message')?.value || '';

            const whatsappMessage = `
*New Equipment Rental Inquiry*

*Name:* ${fullName}
*Company:* ${companyName}
*Email:* ${email}
*Phone:* ${phone}
*Location:* ${projectLocation}
*Project Type:* ${projectType}
*Equipment Needed:* ${equipmentNeeded}
*Rental Model:* ${rentalModel}
*Start Date:* ${startDate}
*Additional Details:* ${message}

Please provide a quote for the above requirements.
            `.trim();

            const encodedMessage = encodeURIComponent(whatsappMessage);
            const whatsappURL = `https://wa.me/919289375999?text=${encodedMessage}`;

            window.alert('Thank you for your inquiry! Redirecting to WhatsApp to complete your request.');
            window.open(whatsappURL, '_blank');
            contactForm.reset();
        });

        const validateEmail = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
        const validatePhone = (value) => /^[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}$/.test(value.replace(/\s/g, ''));

        document.getElementById('email')?.addEventListener('blur', (event) => {
            const target = event.target;
            if (target.value && !validateEmail(target.value)) {
                target.style.borderColor = 'var(--danger)';
                window.alert('Please enter a valid email address');
            } else {
                target.style.borderColor = '#e0e0e0';
            }
        });

        document.getElementById('phone')?.addEventListener('blur', (event) => {
            const target = event.target;
            if (target.value && !validatePhone(target.value)) {
                target.style.borderColor = 'var(--danger)';
                window.alert('Please enter a valid phone number');
            } else {
                target.style.borderColor = '#e0e0e0';
            }
        });
    };

    const initCounters = () => {
        const dashboardStats = document.querySelector('.dashboard-stats');
        if (!dashboardStats) {
            return;
        }

        const animateCounter = (element, target, duration) => {
            let current = 0;
            const increment = target / (duration / 16);
            const timer = window.setInterval(() => {
                current += increment;
                if (current >= target) {
                    element.textContent = target;
                    window.clearInterval(timer);
                } else {
                    element.textContent = Math.floor(current);
                }
            }, 16);
        };

        const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.querySelectorAll('.stat-number').forEach((stat) => {
                        const target = parseInt(stat.textContent.replace(/,/g, ''), 10);
                        if (Number.isFinite(target)) {
                            animateCounter(stat, target, 2000);
                        }
                    });
                    obs.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });

        observer.observe(dashboardStats);
    };

    const updateEquipmentStatus = () => {
        document.querySelectorAll('.item-status').forEach((status) => {
            if (status.classList.contains('available')) {
                status.style.animation = 'pulse 2s infinite';
            }
        });
    };

    const initPrintingState = () => {
        window.addEventListener('beforeprint', () => {
            document.body.classList.add('printing');
        });

        window.addEventListener('afterprint', () => {
            document.body.classList.remove('printing');
        });
    };

    const setupImageLazyLoad = () => {
        if (!('IntersectionObserver' in window)) {
            return;
        }

        const imageObserver = new IntersectionObserver((entries, obs) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    if (img.dataset.src) {
                        img.src = img.dataset.src;
                        img.classList.add('loaded');
                    }
                    obs.unobserve(img);
                }
            });
        });

        document.querySelectorAll('img[data-src]').forEach((img) => {
            imageObserver.observe(img);
        });
    };

    const initWelcomeLogs = () => {
        // eslint-disable-next-line no-console
        console.log('%c🏗️ RACH INFRA GearOnRent', 'font-size: 24px; font-weight: bold; color: #FF6B35;');
        // eslint-disable-next-line no-console
        console.log('%cIoT-Enabled Construction Equipment Rental', 'font-size: 14px; color: #1E88E5;');
        // eslint-disable-next-line no-console
        console.log('%c📞 Contact: +91 92893 75999', 'font-size: 12px; color: #666;');
        // eslint-disable-next-line no-console
        console.log('%c🌐 Website: www.rachinfra.com', 'font-size: 12px; color: #666;');
    };

    const initErrorLogging = () => {
        window.addEventListener('error', (event) => {
            // eslint-disable-next-line no-console
            console.error('Application error:', event.error);
        });

        window.addEventListener('unhandledrejection', (event) => {
            // eslint-disable-next-line no-console
            console.error('Unhandled promise rejection:', event.reason);
        });
    };

    const initScrollThrottle = () => {
        const throttle = (func, limit) => {
            let inThrottle;
            return function throttledFn(...args) {
                if (!inThrottle) {
                    func.apply(this, args);
                    inThrottle = true;
                    window.setTimeout(() => {
                        inThrottle = false;
                    }, limit);
                }
            };
        };

        const handleScroll = throttle(() => {
            // placeholder for potential scroll handling enhancements
        }, 100);

        window.addEventListener('scroll', handleScroll);
    };

    const initPageLoad = () => {
        window.addEventListener('load', () => {
            document.body.style.opacity = '0';
            window.setTimeout(() => {
                document.body.style.transition = 'opacity 0.5s ease';
                document.body.style.opacity = '1';
            }, 100);

            document.querySelectorAll('img[data-src]').forEach((img) => {
                img.src = img.dataset.src;
            });
        });
    };

    document.addEventListener('DOMContentLoaded', () => {
        initAOS();
        initScrollEffects();
        initMobileMenu();
        initSmoothScroll();
        setupCalculator();
        setupRateCardDownload();
        setupContactForm();
        initCounters();
        updateEquipmentStatus();
        initPrintingState();
        setupImageLazyLoad();
        initWelcomeLogs();
        initErrorLogging();
        initScrollThrottle();
        initPageLoad();

        window.setInterval(updateEquipmentStatus, 30000);
    });
})();
