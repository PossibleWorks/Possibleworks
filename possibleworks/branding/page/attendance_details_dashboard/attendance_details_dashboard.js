/* Attendance Dashboard – Possibleworks
   Frappe Page · React 18 + htm · Live Frappe HR data.
*/

frappe.pages["attendance-details-dashboard"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: "Attendance Details Dashboard",
		single_column: true,
	});

	$(wrapper).find(".page-head").hide();

	let mount = wrapper.querySelector("#att-dash-root");
	if (!mount) {
		mount = document.createElement("div");
		mount.id = "att-dash-root";
		wrapper.querySelector(".page-content").appendChild(mount);
	}

	_loadScript("https://unpkg.com/react@18/umd/react.production.min.js")
		.then(() => _loadScript("https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"))
		.then(() => _loadScript("https://unpkg.com/htm/dist/htm.umd.js"))
		.then(() => _mountDashboard(mount))
		.catch((err) => {
			mount.innerHTML = `<div style="padding:40px;color:red">Failed to load: ${err.message}</div>`;
		});
};

function _loadScript(src) {
	if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
	return new Promise((resolve, reject) => {
		const s = document.createElement("script");
		s.src = src;
		s.onload = resolve;
		s.onerror = () => reject(new Error("Could not load " + src));
		document.head.appendChild(s);
	});
}

function _mountDashboard(mount) {
	if (mount._reactRoot) { mount._reactRoot.unmount(); mount._reactRoot = null; }

	const html = htm.bind(React.createElement);
	const { useState, useMemo, useEffect, useCallback } = React;

	const STATUSES = ["Present", "Absent", "Half Day", "On Leave", "Work From Home"];
	const MONTHS   = ["January","February","March","April","May","June","July","August","September","October","November","December"];
	const ABBR     = { Present:"P", Absent:"A", "Half Day":"H", "On Leave":"L", "Work From Home":"W" };

	const STATUS_COLORS = {
		Present:          { bg:"#dcfce7", fg:"#166534", dot:"#22c55e" },
		Absent:           { bg:"#fee2e2", fg:"#991b1b", dot:"#ef4444" },
		"Half Day":       { bg:"#fef9c3", fg:"#854d0e", dot:"#eab308" },
		"On Leave":       { bg:"#e0e7ff", fg:"#3730a3", dot:"#6366f1" },
		"Work From Home": { bg:"#fff7ed", fg:"#c2410c", dot:"#f97316" },
	};

	const headerSelectStyle = {
		background:"#fff", border:"1px solid #d1d5db", borderRadius:8, padding:"8px 14px",
		color:"#1a1a2e", fontSize:13, fontFamily:"'DM Sans',sans-serif", cursor:"pointer",
		outline:"none", appearance:"auto", boxShadow:"0 1px 2px rgba(0,0,0,0.05)",
	};
	const filterSelectStyle = {
		background:"#fafaf8", border:"1px solid #e5e7eb", borderRadius:10,
		padding:"10px 14px", fontSize:13, color:"#374151",
		fontFamily:"'DM Sans',sans-serif", cursor:"pointer", outline:"none",
		appearance:"auto", width:"100%",
	};
	const filterInputStyle = { ...filterSelectStyle, appearance:"none", cursor:"text" };
	const thStyle = {
		padding:"10px 12px", textAlign:"left", fontWeight:700, fontSize:10,
		textTransform:"uppercase", letterSpacing:1, color:"#6b7280",
		fontFamily:"'Space Mono',monospace", borderBottom:"2px solid #e5e7eb",
		whiteSpace:"nowrap", cursor:"pointer", userSelect:"none",
	};

	const avatarBg = (id) => `hsl(${(id.charCodeAt(id.length-1)*37)%360},45%,90%)`;
	const avatarFg = (id) => `hsl(${(id.charCodeAt(id.length-1)*37)%360},45%,35%)`;
	const initials = (name) => (name||"?").split(" ").map(n=>n[0]).slice(0,2).join("");

	/* ── custom scrollable dropdown ── */
	function CustomSelect({ value, onChange, options, placeholder, searchable }) {
		const [open, setOpen] = useState(false);
		const [search, setSearch] = useState("");
		const ref = React.useRef(null);
		useEffect(() => {
			const handler = e => { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setSearch(""); } };
			document.addEventListener("mousedown", handler);
			return () => document.removeEventListener("mousedown", handler);
		}, []);
		const sel = options.find(o => o.value === value);

		/* ── searchable: typeahead input ── */
		if (searchable) {
			const filtered = search
				? options.filter(o => o.label.toLowerCase().includes(search.toLowerCase()))
				: options;
			const handleKeyDown = e => {
				if (e.key === "Enter") {
					e.preventDefault();
					const first = filtered.find(o => o.value !== "") || filtered[0];
					if (first) { onChange(first.value); setOpen(false); setSearch(""); }
				} else if (e.key === "Escape") {
					setOpen(false); setSearch("");
				}
			};
			const isFilled = !!value;
			return html`<div ref=${ref} style=${{ position:"relative", width:"100%" }}>
				<div style=${{
					display:"flex", alignItems:"center", gap:0,
					background: isFilled?"#eef2ff":"#fafaf8",
					border: isFilled?"1.5px solid #6366f1":"1px solid #e5e7eb",
					borderRadius:10, minHeight:38, overflow:"hidden",
					transition:"all 0.2s",
				}}>
					<span style=${{ padding:"0 10px", color: isFilled?"#6366f1":"#9ca3af", fontSize:14, flexShrink:0, display:"flex", alignItems:"center" }}>🔍</span>
					<input
						value=${!open && isFilled ? sel.label : search}
						placeholder=${placeholder}
						onFocus=${()=>{ setOpen(true); }}
						onInput=${e=>{ setSearch(e.target.value); setOpen(true); }}
						onKeyDown=${handleKeyDown}
						style=${{
							flex:1, background:"transparent", border:"none", outline:"none",
							padding:"8px 4px", fontSize:13,
							color: isFilled&&!open?"#3730a3":"#374151",
							fontWeight: isFilled&&!open?600:400,
							fontFamily:"'DM Sans',sans-serif", cursor:"text", minWidth:0,
						}}
					/>
					${isFilled
						? html`<span onClick=${e=>{ e.stopPropagation(); onChange(""); setSearch(""); setOpen(false); }}
							style=${{ padding:"0 12px", cursor:"pointer", color:"#6366f1", fontSize:13, display:"flex", alignItems:"center", flexShrink:0 }}>✕</span>`
						: html`<span style=${{ padding:"0 10px", color:"#9ca3af", fontSize:9, pointerEvents:"none", display:"flex", alignItems:"center" }}>${open?"▲":"▼"}</span>`
					}
				</div>
				${open && html`<div style=${{
					position:"absolute", top:"calc(100% + 4px)", left:0, right:"auto",
					minWidth:280, maxWidth:360,
					background:"#fff", border:"1px solid #e5e7eb", borderRadius:12,
					zIndex:1000, boxShadow:"0 8px 24px rgba(0,0,0,0.14)",
					overflow:"hidden",
				}}>
					<div style=${{ padding:"10px 12px", borderBottom:"1px solid #f3f4f6", background:"#fafaf8", fontSize:11, color:"#9ca3af", fontFamily:"'Space Mono',monospace" }}>
						${search ? `${filtered.length} result${filtered.length!==1?"s":""} · Press Enter to select first` : `${filtered.length} employees`}
					</div>
					<div style=${{maxHeight:220,overflowY:"auto"}}>
					${filtered.length===0
						? html`<div style=${{padding:"16px 14px",color:"#9ca3af",fontSize:13,textAlign:"center"}}>No employees found</div>`
						: filtered.map((o,idx) => html`<div key=${o.value}
							onClick=${()=>{ onChange(o.value); setOpen(false); setSearch(""); }}
							onMouseEnter=${e=>e.currentTarget.style.background="#f0f0ec"}
							onMouseLeave=${e=>e.currentTarget.style.background=o.value===value?"#e8e5df":idx===0&&search?"#f5f3ff":"transparent"}
							style=${{
								padding:"9px 14px", fontSize:13, cursor:"pointer",
								color:o.value===value?"#1a1a2e":"#374151",
								background:o.value===value?"#e8e5df":idx===0&&search?"#f5f3ff":"transparent",
								fontWeight:o.value===value?700:400,
								borderLeft: idx===0&&search?"3px solid #7c3aed":"3px solid transparent",
							}}>${o.label}</div>`)
					}
					</div>
				</div>`}
			</div>`;
		}

		/* ── non-searchable: click dropdown ── */
		const filtered = options;
		return html`<div ref=${ref} style=${{ position:"relative", width:"100%" }}>
			<div onClick=${()=>setOpen(!open)} style=${{
				background:"#fafaf8", border:"1px solid #e5e7eb", borderRadius:10,
				padding:"8px 14px", fontSize:13, color:value?"#374151":"#9ca3af",
				fontFamily:"'DM Sans',sans-serif", cursor:"pointer",
				display:"flex", justifyContent:"space-between", alignItems:"center",
				userSelect:"none", minHeight:38,
			}}>
				<span style=${{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>${sel ? sel.label : placeholder}</span>
				<span style=${{fontSize:9,color:"#9ca3af",marginLeft:6,flexShrink:0}}>${open?"▲":"▼"}</span>
			</div>
			${open && html`<div style=${{
				position:"absolute", top:"calc(100% + 4px)", left:0, right:0,
				background:"#fff", border:"1px solid #e5e7eb", borderRadius:10,
				zIndex:1000, boxShadow:"0 4px 16px rgba(0,0,0,0.12)",
			}}>
				<div style=${{maxHeight:220,overflowY:"auto"}}>
				${filtered.map(o => html`<div key=${o.value}
					onClick=${()=>{ onChange(o.value); setOpen(false); }}
					onMouseEnter=${e=>e.currentTarget.style.background="#f0f0ec"}
					onMouseLeave=${e=>e.currentTarget.style.background=o.value===value?"#e8e5df":"transparent"}
					style=${{
						padding:"9px 14px", fontSize:13, cursor:"pointer",
						color:o.value===value?"#1a1a2e":"#374151",
						background:o.value===value?"#e8e5df":"transparent",
						fontWeight:o.value===value?700:400,
					}}>${o.label}</div>`)}
				</div>
			</div>`}
		</div>`;
	}

	/* ── multi-select dropdown ── */
	function MultiSelect({ values, onChange, options, placeholder }) {
		const [open, setOpen] = useState(false);
		const ref = React.useRef(null);
		useEffect(() => {
			const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
			document.addEventListener("mousedown", handler);
			return () => document.removeEventListener("mousedown", handler);
		}, []);
		const toggle = (val) => {
			if (values.includes(val)) onChange(values.filter(v => v !== val));
			else onChange([...values, val]);
		};
		const label = values.length === 0 ? placeholder : values.length === 1 ? values[0] : values.length + " selected";
		return html`<div ref=${ref} style=${{ position:"relative", width:"100%" }}>
			<div onClick=${()=>setOpen(!open)} style=${{
				background:values.length?"#eef2ff":"#fafaf8", border:values.length?"1.5px solid #6366f1":"1px solid #e5e7eb", borderRadius:10,
				padding:"8px 14px", fontSize:13, color:values.length?"#374151":"#9ca3af",
				fontFamily:"'DM Sans',sans-serif", cursor:"pointer",
				display:"flex", justifyContent:"space-between", alignItems:"center",
				userSelect:"none", minHeight:38, transition:"all 0.2s",
			}}>
				<span style=${{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>${label}</span>
				<span style=${{display:"flex",alignItems:"center",gap:4,flexShrink:0}}>
					${values.length > 0 && html`<span onClick=${e=>{e.stopPropagation();onChange([]);}} style=${{color:"#6366f1",fontSize:13,cursor:"pointer",display:"flex",alignItems:"center"}}>✕</span>`}
					<span style=${{fontSize:9,color:"#9ca3af"}}>${open?"▲":"▼"}</span>
				</span>
			</div>
			${open && html`<div style=${{
				position:"absolute", top:"calc(100% + 4px)", left:0, right:0,
				background:"#fff", border:"1px solid #e5e7eb", borderRadius:10,
				zIndex:1000, boxShadow:"0 4px 16px rgba(0,0,0,0.12)",
			}}>
				<div style=${{maxHeight:220,overflowY:"auto"}}>
				${options.map(o => html`<div key=${o.value}
					onClick=${()=>toggle(o.value)}
					onMouseEnter=${e=>e.currentTarget.style.background="#f0f0ec"}
					onMouseLeave=${e=>e.currentTarget.style.background=values.includes(o.value)?"#e8e5df":"transparent"}
					style=${{
						padding:"9px 14px", fontSize:13, cursor:"pointer",
						display:"flex", alignItems:"center", gap:8,
						color:values.includes(o.value)?"#1a1a2e":"#374151",
						background:values.includes(o.value)?"#e8e5df":"transparent",
						fontWeight:values.includes(o.value)?700:400,
					}}>
					<span style=${{
						display:"inline-block", width:16, height:16, borderRadius:4, flexShrink:0,
						border:values.includes(o.value)?"none":"1.5px solid #d1d5db",
						background:values.includes(o.value)?"#6366f1":"#fff",
						color:"#fff", fontSize:10, lineHeight:"16px", textAlign:"center", fontWeight:700,
					}}>${values.includes(o.value)?"✓":""}</span>
					${o.label}
				</div>`)}
				</div>
			</div>`}
		</div>`;
	}

	/* ── employee detail component ── */
	function EmployeeDetail({ emp, records, lpCount, apCount, lpEntries, apEntries, onBack, onRefresh }) {

		const [selectedDates, setSelectedDates] = useState({});
		const [selectedApproveDates, setSelectedApproveDates] = useState({});
		const [leaveTypes, setLeaveTypes] = useState([]);
		const [leaveBalances, setLeaveBalances] = useState([]);

		/* Pre-fetch leave types + balances on mount so Apply Leave opens instantly */
		useEffect(() => {
			frappe.call({ method:"frappe.client.get_list", args:{ doctype:"Leave Type", fields:["name"], limit:50 },
				callback: r => { setLeaveTypes((r.message||[]).map(l=>l.name)); }
			});
			frappe.call({ method:"possibleworks.branding.page.attendance_details_dashboard.attendance_details_dashboard.get_leave_balances",
				args:{ employee: emp.id },
				callback: r => { setLeaveBalances(r.message || []); }
			});
		}, [emp.id]);

		const toggleDate = (date) => {
			setSelectedDates(prev => {
				const next = Object.assign({}, prev);
				if (next[date]) { delete next[date]; } else { next[date] = true; }
				return next;
			});
		};
		const checkableStatuses = { "Absent":true, "Half Day":true };
		/* helper: check if a date has LP or AP */
		const dateHasLP = (dt) => !!(lpEntries||[]).find(l => dt >= l.from && dt <= l.to);
		const dateHasAP = (dt) => !!(apEntries||[]).find(a => dt >= a.from && dt <= a.to);
		/* checkable for Apply Leave/Reg: Absent/Half Day without LP/AP */
		const checkableRecords = records.filter(r => {
			if (!checkableStatuses[r.status]) return false;
			if (dateHasLP(r.date) || dateHasAP(r.date)) return false;
			return true;
		});
		/* checkable for Approve: dates with LP or AP */
		const approvableRecords = records.filter(r => dateHasLP(r.date) || dateHasAP(r.date));
		const toggleAll = () => {
			const allKeys = checkableRecords.map(r => r.date);
			const allSelected = allKeys.length > 0 && allKeys.every(d => selectedDates[d]);
			if (allSelected) { setSelectedDates({}); }
			else {
				const next = {};
				allKeys.forEach(d => { next[d] = true; });
				setSelectedDates(next);
			}
		};
		const selCount = Object.keys(selectedDates).length;
		const allChecked = checkableRecords.length > 0 && checkableRecords.every(r => selectedDates[r.date]);

		/* approve selection */
		const toggleApproveDate = (date) => {
			setSelectedApproveDates(prev => {
				const next = Object.assign({}, prev);
				if (next[date]) { delete next[date]; } else { next[date] = true; }
				return next;
			});
		};
		const toggleAllApprove = () => {
			const allKeys = approvableRecords.map(r => r.date);
			const allSel = allKeys.length > 0 && allKeys.every(d => selectedApproveDates[d]);
			if (allSel) { setSelectedApproveDates({}); }
			else {
				const next = {};
				allKeys.forEach(d => { next[d] = true; });
				setSelectedApproveDates(next);
			}
		};
		const approveCount = Object.keys(selectedApproveDates).length;
		const allApproveChecked = approvableRecords.length > 0 && approvableRecords.every(r => selectedApproveDates[r.date]);

		const handleApprove = () => {
			const selDates = Object.keys(selectedApproveDates).sort();
			if (!selDates.length) { frappe.msgprint("Please select at least one LP/AP date to approve"); return; }
			const lpDates = selDates.filter(d => dateHasLP(d));
			const apDates = selDates.filter(d => !dateHasLP(d) && dateHasAP(d));
			const desc = [];
			if (lpDates.length) desc.push(lpDates.length + " leave(s)");
			if (apDates.length) desc.push(apDates.length + " attendance request(s)");
			frappe.confirm("Approve " + desc.join(" and ") + "?", () => {
				let done = 0; const total = (lpDates.length ? 1 : 0) + (apDates.length ? 1 : 0);
				const onDone = () => { done++; if (done >= total) { setSelectedApproveDates({}); frappe.show_alert({ message:"Approved successfully", indicator:"green" }); if (onRefresh) onRefresh(); } };
				if (lpDates.length) {
					frappe.call({
						method:"possibleworks.branding.page.attendance_details_dashboard.attendance_details_dashboard.approve_pending_leaves",
						args:{ employee: emp.id, dates: JSON.stringify(lpDates) },
						callback: onDone, error: onDone
					});
				}
				if (apDates.length) {
					frappe.call({
						method:"possibleworks.branding.page.attendance_details_dashboard.attendance_details_dashboard.approve_pending_att_requests",
						args:{ employee: emp.id, dates: JSON.stringify(apDates) },
						callback: onDone, error: onDone
					});
				}
			});
		};

		const openLeaveModal = () => {
			const selDates = Object.keys(selectedDates).sort();
			if (!selDates.length) { frappe.msgprint("Please select at least one date from the table"); return; }
			const balHtml = leaveBalances.length
				? "<table style='width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px'>"
				  + "<tr style='background:#f9fafb'>"
				  + "<th style='text-align:left;padding:6px 10px;border-bottom:1px solid #e5e7eb'>Leave Type</th>"
				  + "<th style='text-align:right;padding:6px 10px;border-bottom:1px solid #e5e7eb'>Allocated</th>"
				  + "<th style='text-align:right;padding:6px 10px;border-bottom:1px solid #e5e7eb'>Taken</th>"
				  + "<th style='text-align:right;padding:6px 10px;border-bottom:1px solid #e5e7eb'>Balance</th>"
				  + "</tr>"
				  + leaveBalances.map(b => {
				      const bal = parseFloat(b.balance) || 0;
				      const balColor = bal <= 0 ? "#dc2626" : "#16a34a";
				      return "<tr>"
				        + "<td style='padding:6px 10px;border-bottom:1px solid #f3f4f6'>" + b.leave_type + "</td>"
				        + "<td style='text-align:right;padding:6px 10px;border-bottom:1px solid #f3f4f6'>" + (b.allocated || 0) + "</td>"
				        + "<td style='text-align:right;padding:6px 10px;border-bottom:1px solid #f3f4f6'>" + (b.taken || 0) + "</td>"
				        + "<td style='text-align:right;padding:6px 10px;border-bottom:1px solid #f3f4f6;font-weight:700;color:" + balColor + "'>" + bal + "</td>"
				        + "</tr>";
				    }).join("")
				  + "</table>"
				: "<p style='color:#9ca3af;font-size:13px'>No active leave allocations found for this employee</p>";
			const dateCheckboxes = selDates.map(dt =>
				"<label style='display:inline-flex;align-items:center;gap:6px;padding:4px 10px;font-size:12px;cursor:pointer;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;margin:3px'>"
				+ "<input type='checkbox' checked data-date='" + dt + "' style='width:14px;height:14px;accent-color:#4f46e5;cursor:pointer'/>"
				+ "<span style='font-family:Space Mono,monospace'>" + dt + "</span></label>"
			).join("");
			/* build balance lookup */
			const balMap = {};
			leaveBalances.forEach(b => { balMap[b.leave_type] = parseFloat(b.balance) || 0; });
			const updateValidation = (dlg) => {
				const lt = dlg.get_value("leave_type");
				const el = dlg.$wrapper.find(".leave-validation-msg");
				if (!lt) { el.html(""); dlg.disable_primary_action(); return; }
				let checkedCount = 0;
				dlg.$wrapper.find("input[data-date]").each(function() { if (this.checked) checkedCount++; });
				if (!checkedCount) { el.html(""); dlg.disable_primary_action(); return; }
				const bal = balMap[lt];
				if (bal === undefined) {
					el.html("<div style='padding:8px 12px;background:#fef3c7;color:#92400e;border-radius:6px;font-size:13px;margin-top:8px'>No allocation found for <b>" + lt + "</b></div>");
					dlg.disable_primary_action();
				} else if (checkedCount > bal) {
					el.html("<div style='padding:8px 12px;background:#fee2e2;color:#991b1b;border-radius:6px;font-size:13px;margin-top:8px'>Selected <b>" + checkedCount + " day(s)</b> but only <b>" + bal + "</b> " + lt + " balance available. Please uncheck some dates.</div>");
					dlg.disable_primary_action();
				} else {
					el.html("<div style='padding:8px 12px;background:#dcfce7;color:#166534;border-radius:6px;font-size:13px;margin-top:8px'>" + lt + ": <b>" + checkedCount + "</b> of <b>" + bal + "</b> days will be used</div>");
					dlg.enable_primary_action();
				}
			};
			const d = new frappe.ui.Dialog({
				title: "Apply Leave - " + emp.name,
				fields: [
					{ fieldtype:"HTML", fieldname:"bal_html", options: "<div style='margin-bottom:12px'><div style='font-weight:700;font-size:13px;margin-bottom:6px'>Leave Balances</div>" + balHtml + "</div>" },
					{ fieldtype:"HTML", fieldname:"dates_html", options: "<div style='margin-bottom:12px'><div style='font-weight:700;font-size:13px;margin-bottom:6px'>Select Dates (" + selDates.length + ")</div><div style='display:flex;flex-wrap:wrap'>" + dateCheckboxes + "</div></div>" },
					{ fieldtype:"Select", fieldname:"leave_type", label:"Leave Type", options:leaveTypes.join("\n"), reqd:1, change: function() { updateValidation(d); } },
					{ fieldtype:"HTML", fieldname:"validation_msg", options: "<div class='leave-validation-msg'></div>" },
					{ fieldtype:"Small Text", fieldname:"reason", label:"Reason" },
				],
				size: "large",
				primary_action_label: "Submit Leave",
				primary_action(vals) {
					const checked = [];
					d.$wrapper.find("input[data-date]").each(function() {
						if (this.checked) checked.push(this.getAttribute("data-date"));
					});
					if (!checked.length) { frappe.msgprint("Please select at least one date"); return; }
					/* validate leave balance */
					const selectedType = vals.leave_type;
					const available = balMap[selectedType];
					if (available !== undefined && checked.length > available) {
						frappe.msgprint({
							title: "Insufficient Leave Balance",
							message: "You selected <b>" + checked.length + " day(s)</b> but only have <b>" + available + "</b> " + selectedType + " balance remaining. Please uncheck some dates or choose a different leave type.",
							indicator: "red"
						});
						return;
					}
					d.disable_primary_action();
					let submitted = 0; let failed = 0; const total = checked.length;
					const submitNext = (idx) => {
						if (idx >= total) {
							d.hide();
							setSelectedDates({});
							if (failed) frappe.show_alert({ message: submitted + " of " + total + " leave applications submitted (" + failed + " failed)", indicator:"orange" });
							else frappe.show_alert({ message: total + " leave application(s) submitted", indicator:"green" });
							if (onRefresh) onRefresh();
							return;
						}
						frappe.call({
							method:"possibleworks.branding.page.attendance_details_dashboard.attendance_details_dashboard.submit_leave_application",
							args:{ employee:emp.id, employee_name:emp.name,
								from_date:checked[idx], to_date:checked[idx],
								leave_type:vals.leave_type, description:vals.reason||"" },
							callback: () => { submitted++; submitNext(idx+1); },
							error: () => { failed++; submitNext(idx+1); }
						});
					};
					submitNext(0);
				}
			});
			d.show();
			/* re-validate when date checkboxes change */
			d.$wrapper.on("change", "input[data-date]", function() { updateValidation(d); });
		};

		const openRegModal = () => {
			const selDates = Object.keys(selectedDates).sort();
			if (!selDates.length) { frappe.msgprint("Please select at least one date from the table"); return; }
			const dateCheckboxes = selDates.map(dt =>
				"<label style='display:inline-flex;align-items:center;gap:6px;padding:4px 10px;font-size:12px;cursor:pointer;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;margin:3px'>"
				+ "<input type='checkbox' checked data-date='" + dt + "' style='width:14px;height:14px;accent-color:#166534;cursor:pointer'/>"
				+ "<span style='font-family:Space Mono,monospace'>" + dt + "</span></label>"
			).join("");
			const d = new frappe.ui.Dialog({
				title: "Apply Regularization - " + emp.name,
				size: "large",
				fields: [
					{ fieldtype:"HTML", fieldname:"dates_html", options: "<div style='margin-bottom:12px'><div style='font-weight:700;font-size:13px;margin-bottom:6px'>Select Dates (" + selDates.length + ")</div><div style='display:flex;flex-wrap:wrap'>" + dateCheckboxes + "</div></div>" },
					{ fieldtype:"Time", fieldname:"in_time", label:"In Time" },
					{ fieldtype:"Time", fieldname:"out_time", label:"Out Time" },
					{ fieldtype:"Small Text", fieldname:"reason", label:"Reason", reqd:1 },
				],
				primary_action_label: "Submit",
				primary_action(vals) {
					const checked = [];
					d.$wrapper.find("input[data-date]").each(function() {
						if (this.checked) checked.push(this.getAttribute("data-date"));
					});
					if (!checked.length) { frappe.msgprint("Please select at least one date"); return; }
					d.disable_primary_action();
					let submitted = 0; let failed = 0; const total = checked.length;
					const submitNext = (idx) => {
						if (idx >= total) {
							d.hide();
							setSelectedDates({});
							if (failed) frappe.show_alert({ message: submitted + " of " + total + " requests submitted (" + failed + " failed)", indicator:"orange" });
							else frappe.show_alert({ message: total + " attendance request(s) submitted", indicator:"green" });
							if (onRefresh) onRefresh();
							return;
						}
						const dt = checked[idx];
						frappe.call({
							method:"frappe.client.insert",
							args:{ doc:{ doctype:"Attendance Request", employee:emp.id, employee_name:emp.name,
								from_date:dt, to_date:dt, reason:vals.reason,
								...(vals.in_time ? { in_time: dt+" "+vals.in_time } : {}),
								...(vals.out_time ? { out_time: dt+" "+vals.out_time } : {}) }},
							callback: () => { submitted++; submitNext(idx+1); },
							error: () => { failed++; submitNext(idx+1); }
						});
					};
					submitNext(0);
				}
			});
			d.show();
		};

		/* helper: status badge for a record row */
		function statusBadge(r) {
			const lp = (lpEntries||[]).find(l => r.date >= l.from && r.date <= l.to);
			if (lp) return html`<span style=${{ background:"#ffedd5", color:"#9a3412", padding:"3px 10px", borderRadius:6, fontWeight:600, fontSize:12 }}>Leave Pending</span>`;
			const ap = (apEntries||[]).find(a => r.date >= a.from && r.date <= a.to);
			if (ap) return html`<span style=${{ background:"#cffafe", color:"#0e7490", padding:"3px 10px", borderRadius:6, fontWeight:600, fontSize:12 }}>Attn. Request</span>`;
			const sc = STATUS_COLORS[r.status] || { bg:"#f3f4f6", fg:"#374151" };
			return html`<span style=${{ background:sc.bg, color:sc.fg, padding:"3px 10px", borderRadius:6, fontWeight:600, fontSize:12 }}>${r.status}</span>`;
		}

		/* helper: yes/no badge */
		function yesNo(flag, yesColor) {
			if (flag) return html`<span style=${{ color:yesColor, fontWeight:700, fontSize:12 }}>Yes</span>`;
			return html`<span style=${{ color:"#9ca3af", fontSize:12 }}>No</span>`;
		}

		/* pre-build employee info string */
		const empSubtitle = emp.id + (emp.department ? " \u00b7 " + emp.department : "") + (emp.designation ? " \u00b7 " + emp.designation : "") + (emp.branch ? " \u00b7 " + emp.branch : "");

		/* pre-build status summary cards */
		const statusCards = STATUSES.map(s => {
			const sc = STATUS_COLORS[s];
			return html`<div key=${s} style=${{ textAlign:"center", padding:"14px 8px", borderRadius:10, background:sc.bg }}>
				<div style=${{ fontSize:24, fontWeight:700, color:sc.fg, fontFamily:"'Space Mono',monospace" }}>${emp[s]}</div>
				<div style=${{ fontSize:11, color:sc.fg, marginTop:4, fontWeight:600 }}>${s}</div>
			</div>`;
		});

		/* pre-build table rows with checkboxes */
		const tdStyle = { padding:"10px 14px", borderBottom:"1px solid #f3f4f6" };
		const monoTd = Object.assign({}, tdStyle, { fontFamily:"'Space Mono',monospace", color:"#6b7280" });
		const cbStyle = { width:16, height:16, cursor:"pointer", accentColor:"#4f46e5" };
		const tableRows = records.map((r, i) => {
			const bg = i % 2 === 0 ? "#fff" : "#fafaf8";
			const hasLP = dateHasLP(r.date);
			const hasAP = dateHasAP(r.date);
			const isApprovable = hasLP || hasAP;
			const canCheck = !!checkableStatuses[r.status] && !isApprovable;
			const checked = canCheck ? !!selectedDates[r.date] : (isApprovable ? !!selectedApproveDates[r.date] : false);
			const onToggle = isApprovable ? () => toggleApproveDate(r.date) : (canCheck ? () => toggleDate(r.date) : null);
			const cbCell = (canCheck || isApprovable)
				? html`<td style=${tdStyle}><input type="checkbox" checked=${checked} onChange=${onToggle} style=${{ width:16, height:16, cursor:"pointer", accentColor: isApprovable ? "#f59e0b" : "#4f46e5" }} /></td>`
				: html`<td style=${tdStyle}></td>`;
			return html`<tr key=${r.date}
				onMouseEnter=${e => { e.currentTarget.style.background = "#f9fafb"; }}
				onMouseLeave=${e => { e.currentTarget.style.background = checked ? "#f0f0ff" : bg; }}
				style=${{ background: checked ? "#f0f0ff" : bg, cursor:"pointer", transition:"background 0.15s" }}>
				${cbCell}
				<td style=${monoTd}>${r.date}</td>
				<td style=${tdStyle}>${statusBadge(r)}</td>
				<td style=${monoTd}>${r.checkin || "\u2014"}</td>
				<td style=${monoTd}>${r.checkout || "\u2014"}</td>
				<td style=${tdStyle}>${yesNo(r.lateEntry, "#dc2626")}</td>
				<td style=${tdStyle}>${yesNo(r.earlyExit, "#f59e0b")}</td>
			</tr>`;
		});

		const emptyRow = records.length === 0
			? html`<tr><td colSpan="7" style=${{ padding:"30px", textAlign:"center", color:"#9ca3af" }}>No records found</td></tr>`
			: null;

		const thStyle = { padding:"10px 14px", textAlign:"left", fontWeight:700, fontSize:11, textTransform:"uppercase", letterSpacing:1, color:"#6b7280", fontFamily:"'Space Mono',monospace", borderBottom:"2px solid #e5e7eb" };
		const selectAllTh = html`<th style=${thStyle}><input type="checkbox" checked=${allChecked} onChange=${toggleAll} style=${cbStyle} /></th>`;
		const headers = ["Date","Status","Check-in","Check-out","Late Entry","Early Exit"].map(h =>
			html`<th key=${h} style=${thStyle}>${h}</th>`
		);

		const selLabel = selCount > 0 ? " (" + selCount + " selected)" : "";

		return html`<div>
			<div style=${{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:18, flexWrap:"wrap", gap:10 }}>
				<button onClick=${onBack} style=${{ background:"#fff", border:"1px solid #e5e7eb", borderRadius:10, padding:"8px 18px", fontSize:13, fontWeight:600, cursor:"pointer", display:"flex", alignItems:"center", gap:6 }}>${"\u2190"} Back to Workday Tracker</button>
				<div style=${{ display:"flex", gap:10 }}>
					<button onClick=${openLeaveModal} style=${{ background:"#e0e7ff", color:"#3730a3", border:"none", borderRadius:8, padding:"8px 18px", fontSize:13, fontWeight:700, cursor:"pointer" }}>Apply Leave${selLabel}</button>
					<button onClick=${openRegModal} style=${{ background:"#dcfce7", color:"#166534", border:"none", borderRadius:8, padding:"8px 18px", fontSize:13, fontWeight:700, cursor:"pointer" }}>Apply Regularization${selLabel}</button>
					${approveCount > 0 && html`<button onClick=${handleApprove} style=${{ background:"#fef3c7", color:"#92400e", border:"none", borderRadius:8, padding:"8px 18px", fontSize:13, fontWeight:700, cursor:"pointer" }}>Approve (${approveCount})</button>`}
				</div>
			</div>
			<div style=${{ background:"#fff", borderRadius:14, border:"1px solid #e8e5df", overflow:"hidden" }}>
				<div style=${{ padding:"24px 28px", borderBottom:"1px solid #e8e5df", display:"flex", alignItems:"center", gap:18 }}>
					<div style=${{ width:56, height:56, borderRadius:14, background:avatarBg(emp.id), display:"flex", alignItems:"center", justifyContent:"center", fontWeight:700, fontSize:20, color:avatarFg(emp.id) }}>${initials(emp.name)}</div>
					<div>
						<div style=${{ fontSize:20, fontWeight:700 }}>${emp.name}</div>
						<div style=${{ color:"#6b7280", fontSize:13 }}>${empSubtitle}</div>
					</div>
				</div>
				<div style=${{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(130px,1fr))", gap:12, padding:"20px 28px" }}>
					${statusCards}
					<div style=${{ textAlign:"center", padding:"14px 8px", borderRadius:10, background:"#ffedd5" }}>
						<div style=${{ fontSize:24, fontWeight:700, color:"#9a3412", fontFamily:"'Space Mono',monospace" }}>${lpCount || 0}</div>
						<div style=${{ fontSize:11, color:"#9a3412", marginTop:4, fontWeight:600 }}>Leave Pending</div>
					</div>
					<div style=${{ textAlign:"center", padding:"14px 8px", borderRadius:10, background:"#cffafe" }}>
						<div style=${{ fontSize:24, fontWeight:700, color:"#0e7490", fontFamily:"'Space Mono',monospace" }}>${apCount || 0}</div>
						<div style=${{ fontSize:11, color:"#0e7490", marginTop:4, fontWeight:600 }}>Attn. Request</div>
					</div>
				</div>
				<div style=${{ padding:"0 28px 24px", overflowX:"auto" }}>
					<table style=${{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
						<thead><tr style=${{ background:"#f9fafb" }}>${selectAllTh}${headers}</tr></thead>
						<tbody>${emptyRow}${tableRows}</tbody>
					</table>
				</div>
			</div>
		</div>`;
	}

	/* ── custom date picker ── */
	function DatePicker({ value, onChange, label }) {
		const parsed = value ? new Date(value + "T00:00:00") : new Date();
		const [open, setOpen] = useState(false);
		const [pos, setPos] = useState({ top:0, left:0 });
		const [viewYear, setViewYear] = useState(parsed.getFullYear());
		const [viewMonth, setViewMonth] = useState(parsed.getMonth());
		const [mode, setMode] = useState("day"); /* day | month | year */
		const triggerRef = React.useRef(null);
		useEffect(() => {
			const h = e => {
				const portal = document.getElementById("dp-portal-"+label);
				if (triggerRef.current && !triggerRef.current.contains(e.target) && (!portal || !portal.contains(e.target))) {
					setOpen(false); setMode("day");
				}
			};
			document.addEventListener("mousedown", h);
			return () => document.removeEventListener("mousedown", h);
		}, []);
		useEffect(() => {
			if (value) { const d = new Date(value+"T00:00:00"); setViewYear(d.getFullYear()); setViewMonth(d.getMonth()); }
		}, [value]);
		const openCalendar = () => {
			if (triggerRef.current) {
				const r = triggerRef.current.getBoundingClientRect();
				setPos({ top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 310) });
			}
			setOpen(o => !o); setMode("day");
		};
		const MO_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
		const DOW = ["Su","Mo","Tu","We","Th","Fr","Sa"];
		const firstDay = new Date(viewYear, viewMonth, 1).getDay();
		const totalDays = new Date(viewYear, viewMonth+1, 0).getDate();
		const cells = [...Array(firstDay).fill(null), ...Array.from({length:totalDays},(_,i)=>i+1)];
		const prevM = () => { if(viewMonth===0){setViewMonth(11);setViewYear(y=>y-1);}else setViewMonth(m=>m-1); };
		const nextM = () => { if(viewMonth===11){setViewMonth(0);setViewYear(y=>y+1);}else setViewMonth(m=>m+1); };
		const selectDay = day => { const m=String(viewMonth+1).padStart(2,"0"),d=String(day).padStart(2,"0"); onChange(`${viewYear}-${m}-${d}`); setOpen(false); setMode("day"); };
		const selD = value ? new Date(value+"T00:00:00") : null;
		const fmt = v => { if(!v) return "—"; const d=new Date(v+"T00:00:00"); return `${MO_SHORT[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; };
		const today = new Date();
		const yearRange = Array.from({length:12}, (_,i) => viewYear - 5 + i);
		const btnStyle = { background:"none", border:"none", borderRadius:8, width:34, height:34, cursor:"pointer", fontSize:18, color:"#64748b", display:"flex", alignItems:"center", justifyContent:"center", fontWeight:700, flexShrink:0 };

		const calendar = open && html`<div id=${"dp-portal-"+label} style=${{
			position:"fixed", top:pos.top, left:pos.left, zIndex:9999,
			background:"#fff", borderRadius:16, boxShadow:"0 16px 48px rgba(0,0,0,0.18)",
			padding:"16px 16px 12px", width:288, border:"1px solid #e2e8f0",
		}}>
			<!-- header: ‹  Month Year  › -->
			<div style=${{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:14 }}>
				<button onClick=${mode==="day"?prevM:()=>setViewYear(y=>y-1)} style=${btnStyle}>‹</button>
				<div style=${{ display:"flex", alignItems:"center", gap:6 }}>
					<button onClick=${()=>setMode(mode==="month"?"day":"month")} style=${{
						background:mode==="month"?"#eef2ff":"transparent", border:"none", borderRadius:8,
						padding:"4px 10px", cursor:"pointer", fontWeight:700, fontSize:14, color:"#0f172a",
					}}>${MONTHS[viewMonth]}</button>
					<button onClick=${()=>setMode(mode==="year"?"day":"year")} style=${{
						background:mode==="year"?"#eef2ff":"transparent", border:"none", borderRadius:8,
						padding:"4px 10px", cursor:"pointer", fontWeight:700, fontSize:14, color:"#475569",
					}}>${viewYear}</button>
				</div>
				<button onClick=${mode==="day"?nextM:()=>setViewYear(y=>y+1)} style=${btnStyle}>›</button>
			</div>

			<!-- month picker view -->
			${mode==="month" && html`<div style=${{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:6, marginBottom:8 }}>
				${MO_SHORT.map((m,i) => html`<button key=${i} onClick=${()=>{ setViewMonth(i); setMode("day"); }}
					style=${{
						background:i===viewMonth?"#1a1a2e":"#f8fafc",
						color:i===viewMonth?"#fff":"#374151",
						border:"none", borderRadius:10, padding:"10px 0",
						cursor:"pointer", fontWeight:i===viewMonth?700:400, fontSize:13,
					}}>${m}</button>`)}
			</div>`}

			<!-- year picker view -->
			${mode==="year" && html`<div style=${{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:6, marginBottom:8 }}>
				${yearRange.map(y => html`<button key=${y} onClick=${()=>{ setViewYear(y); setMode("day"); }}
					style=${{
						background:y===viewYear?"#1a1a2e":"#f8fafc",
						color:y===viewYear?"#fff":"#374151",
						border:"none", borderRadius:10, padding:"10px 0",
						cursor:"pointer", fontWeight:y===viewYear?700:400, fontSize:13,
					}}>${y}</button>`)}
			</div>`}

			<!-- day grid -->
			${mode==="day" && html`<div>
				<div style=${{ display:"grid", gridTemplateColumns:"repeat(7,1fr)", textAlign:"center", marginBottom:4 }}>
					${DOW.map(d => html`<div key=${d} style=${{ fontSize:10, fontWeight:700, color:"#94a3b8", padding:"3px 0", fontFamily:"'Space Mono',monospace" }}>${d}</div>`)}
				</div>
				<div style=${{ display:"grid", gridTemplateColumns:"repeat(7,1fr)", gap:2 }}>
					${cells.map((day,i) => {
						if (!day) return html`<div key=${i} />`;
						const isSel = selD && day===selD.getDate() && viewMonth===selD.getMonth() && viewYear===selD.getFullYear();
						const isToday = day===today.getDate() && viewMonth===today.getMonth() && viewYear===today.getFullYear();
						return html`<div key=${i} onClick=${()=>selectDay(day)}
							onMouseEnter=${e=>{ if(!isSel) e.currentTarget.style.background="#f1f5f9"; }}
							onMouseLeave=${e=>{ if(!isSel) e.currentTarget.style.background=isToday?"#fef2f2":"transparent"; }}
							style=${{
								textAlign:"center", padding:"8px 0", borderRadius:8, cursor:"pointer",
								fontSize:13, fontWeight:isSel?700:400,
								background:isSel?"#1a1a2e":isToday?"#fef2f2":"transparent",
								color:isSel?"#fff":isToday?"#e94560":"#374151",
								border:isToday&&!isSel?"1px solid #fca5a5":"1px solid transparent",
							}}>${day}</div>`;
					})}
				</div>
			</div>`}

			<div style=${{ display:"flex", justifyContent:"space-between", marginTop:12, paddingTop:10, borderTop:"1px solid #f1f5f9" }}>
				<button onClick=${()=>{onChange("");setOpen(false);setMode("day");}} style=${{ background:"none",border:"none",color:"#94a3b8",fontSize:12,cursor:"pointer" }}>Clear</button>
				<button onClick=${()=>{ const t=new Date(); setViewMonth(t.getMonth()); setViewYear(t.getFullYear()); selectDay(t.getDate()); }} style=${{ background:"none",border:"none",color:"#6366f1",fontWeight:700,fontSize:12,cursor:"pointer" }}>Today</button>
			</div>
		</div>`;

		return html`<div ref=${triggerRef} style=${{ position:"relative" }}>
			<div onClick=${openCalendar} style=${{
				display:"inline-flex", alignItems:"center", gap:8, cursor:"pointer",
				padding:"8px 14px", borderRadius:10,
				background:"#f9fafb", border:"1px solid "+(open?"#6366f1":"#e5e7eb"),
				transition:"all 0.15s", minWidth:140,
				boxShadow: open?"0 0 0 2px rgba(99,102,241,0.1)":"none",
			}}>
				<span style=${{ fontSize:13, color:"#1a1a2e", fontWeight:600 }}>${fmt(value)}</span>
				<span style=${{ color:"#9ca3af", fontSize:9 }}>${open?"▲":"▼"}</span>
			</div>
			${calendar}
		</div>`;
	}

	/* ── main component ── */
	function AttDashboard() {
		const now = new Date();
		const pad2 = n => String(n).padStart(2,"0");
		const defaultFrom = `${now.getFullYear()}-${pad2(now.getMonth()+1)}-01`;
		const defaultTo   = `${now.getFullYear()}-${pad2(now.getMonth()+1)}-${pad2(new Date(now.getFullYear(),now.getMonth()+1,0).getDate())}`;
		const [fromDate, setFromDate] = useState(defaultFrom);
		const [toDate,   setToDate]   = useState(defaultTo);

		const [filterDept,        setFilterDept]        = useState("");
		const [filterBranch,      setFilterBranch]      = useState("");
		const [filterShift,       setFilterShift]       = useState("");
		const [filterDesignation, setFilterDesignation] = useState("");
		const [filterStatus,      setFilterStatus]      = useState([]);
		const [filterEmployee,    setFilterEmployee]    = useState("");
		const [searchQuery,       setSearchQuery]       = useState("");

		const [activeTab,        setActiveTab]        = useState("daily");
		const [selectedEmployee, setSelectedEmployee] = useState(null);
		const [sortField,        setSortField]        = useState("name");
		const [sortDir,          setSortDir]          = useState("asc");
		const [dailyPage,        setDailyPage]        = useState(1);
		const DAILY_PAGE_SIZE = 30;

		const [loading,          setLoading]          = useState(true);
		const [employees,        setEmployees]        = useState([]);
		const [attendance,       setAttendance]       = useState([]);
		const [employeeHolidays, setEmployeeHolidays] = useState({});
		const [leaveRequests,    setLeaveRequests]    = useState({});
		const [attRequests,      setAttRequests]      = useState({});

		/* ── fetch live data ── */
		const fetchData = useCallback((keepEmployee) => {
			setLoading(true);
			if (!keepEmployee) setSelectedEmployee(null);

			frappe.call({
				method: "possibleworks.branding.page.attendance_details_dashboard.attendance_details_dashboard.get_attendance_data",
				args: { from_date: fromDate, to_date: toDate },
				callback(r) {
					if (!r.message) { setLoading(false); return; }

					// build employee id → meta map
					const empMap = {};
					(r.message.employees || []).forEach(e => {
						empMap[e.name] = {
							id:          e.name,
							name:        e.employee_name || e.name,
							department:  e.department   || "",
							designation: e.designation  || "",
							branch:      e.branch       || "",
						};
					});

					const empList = Object.values(empMap);

					const attList = (r.message.attendance || []).filter(a => empMap[a.employee]).map(a => {
						const meta = empMap[a.employee] || {};
						return {
							employee:     a.employee,
							employeeName: a.employee_name || a.employee,
							department:   a.department   || meta.department || "",
							designation:  meta.designation || "",
							branch:       meta.branch      || "",
							shift:        a.shift          || "General",
							date:         a.attendance_date,
							status:       a.status,
							checkin:      a.in_time  ? a.in_time.split(" ")[1].slice(0,5)  : null,
							checkout:     a.out_time ? a.out_time.split(" ")[1].slice(0,5) : null,
							lateEntry:    !!a.late_entry,
							earlyExit:    !!a.early_exit,
						};
					});

					// Build per-employee holiday date sets
					const holidayData = {};
					const rawHolidays = r.message.employee_holidays || {};
					Object.entries(rawHolidays).forEach(([empId, list]) => {
						holidayData[empId] = {
							dates: new Set(list.map(h => h.date)),
							map:   Object.fromEntries(list.map(h => [h.date, h])),
						};
					});

					// Build per-employee leave request ranges (LP)
					const lpData = {};
					(r.message.leave_requests || []).forEach(l => {
						if (!lpData[l.employee]) lpData[l.employee] = [];
						lpData[l.employee].push({ from: l.from_date, to: l.to_date, status: l.status || "Open", leave_type: l.leave_type || "" });
					});
					// Build per-employee attendance request ranges (AP)
					const apData = {};
					(r.message.att_requests || []).forEach(a => {
						if (!apData[a.employee]) apData[a.employee] = [];
						apData[a.employee].push({ from: a.from_date, to: a.to_date, docstatus: a.docstatus, reason: a.reason || "" });
					});

					setEmployees(empList);
					setAttendance(attList);
					setEmployeeHolidays(holidayData);
					setLeaveRequests(lpData);
					setAttRequests(apData);
					setLoading(false);
				},
			});
		}, [fromDate, toDate]);

		useEffect(() => { fetchData(); }, [fetchData]);

		/* ── dynamic filter options from real data ── */
		const departments  = useMemo(() => [...new Set(employees.map(e => e.department).filter(Boolean))].sort(), [employees]);
		const branches     = useMemo(() => [...new Set(employees.map(e => e.branch).filter(Boolean))].sort(), [employees]);
		const shifts       = useMemo(() => [...new Set(attendance.map(a => a.shift).filter(Boolean))].sort(), [attendance]);
		const designations = useMemo(() => [...new Set(employees.map(e => e.designation).filter(Boolean))].sort(), [employees]);

		/* ── filtered attendance (with status filter — for summaries/overview) ── */
		const filteredAttendance = useMemo(() => attendance.filter(r => {
			if (filterDept        && r.department  !== filterDept)        return false;
			if (filterBranch      && r.branch      !== filterBranch)      return false;
			if (filterShift       && r.shift       !== filterShift)       return false;
			if (filterDesignation && r.designation !== filterDesignation) return false;
			if (filterStatus.length && !filterStatus.includes(r.status)) return false;
			if (filterEmployee    && r.employee    !== filterEmployee)    return false;
			if (searchQuery) {
				const q = searchQuery.toLowerCase();
				if (!(r.employeeName||"").toLowerCase().includes(q) && !(r.employee||"").toLowerCase().includes(q)) return false;
			}
			return true;
		}), [attendance, filterDept, filterBranch, filterShift, filterDesignation, filterStatus, filterEmployee, searchQuery]);

		/* ── attendance for daily view cells (no status filter — show all, use opacity to dim) ── */
		const filteredAttendanceCells = useMemo(() => attendance.filter(r => {
			if (filterDept        && r.department  !== filterDept)        return false;
			if (filterBranch      && r.branch      !== filterBranch)      return false;
			if (filterShift       && r.shift       !== filterShift)       return false;
			if (filterDesignation && r.designation !== filterDesignation) return false;
			if (filterEmployee    && r.employee    !== filterEmployee)    return false;
			if (searchQuery) {
				const q = searchQuery.toLowerCase();
				if (!(r.employeeName||"").toLowerCase().includes(q) && !(r.employee||"").toLowerCase().includes(q)) return false;
			}
			return true;
		}), [attendance, filterDept, filterBranch, filterShift, filterDesignation, filterEmployee, searchQuery]);

		const stats = useMemo(() => {
			const s = { Present:0, Absent:0, "Half Day":0, "On Leave":0, "Work From Home":0, total:0, lateEntries:0, earlyExits:0 };
			filteredAttendance.forEach(r => {
				s[r.status] = (s[r.status]||0)+1; s.total++;
				if (r.lateEntry) s.lateEntries++;
				if (r.earlyExit) s.earlyExits++;
			});
			return s;
		}, [filteredAttendance]);

		const rangeDates = useMemo(() => {
			const dates = [];
			const end = new Date(toDate + "T00:00:00");
			const cur = new Date(fromDate + "T00:00:00");
			while (cur <= end) {
				const y = cur.getFullYear();
				const m = String(cur.getMonth()+1).padStart(2,"0");
				const d = String(cur.getDate()).padStart(2,"0");
				dates.push(y + "-" + m + "-" + d);
				cur.setDate(cur.getDate()+1);
			}
			return dates;
		}, [fromDate, toDate]);


		const employeeSummaries = useMemo(() => {
			const map = {};
			filteredAttendance.forEach(r => {
				if (!map[r.employee]) map[r.employee] = {
					id:r.employee, name:r.employeeName, department:r.department,
					branch:r.branch, designation:r.designation, shift:r.shift,
					Present:0, Absent:0, "Half Day":0, "On Leave":0, "Work From Home":0,
					total:0, lateEntries:0, workingDays:0, totalWorkingDays:0,
				};
				map[r.employee][r.status]++;
				map[r.employee].total++;
				if (r.lateEntry) map[r.employee].lateEntries++;
				if (r.status==="Present"||r.status==="Work From Home") map[r.employee].workingDays++;
				if (r.status==="Half Day") map[r.employee].workingDays+=0.5;
			});
			const arr = Object.values(map);
			arr.forEach(emp => {
				const empHol = employeeHolidays[emp.id] || {};
				emp.totalWorkingDays = rangeDates.filter(d => {
					const isSun = new Date(d+"T00:00:00").getDay()===0;
					return !isSun && !(empHol.dates && empHol.dates.has(d));
				}).length;
			});
			arr.sort((a,b) => {
				const va = a[sortField]??a.name, vb = b[sortField]??b.name;
				if (typeof va==="string") return sortDir==="asc"?va.localeCompare(vb):vb.localeCompare(va);
				return sortDir==="asc"?va-vb:vb-va;
			});
			return arr;
		}, [filteredAttendance, sortField, sortDir, employeeHolidays, rangeDates]);

		const deptBreakdown = useMemo(() => {
			const map = {};
			filteredAttendance.forEach(r => {
				const dept = r.department || "Unassigned";
				if (!map[dept]) map[dept] = { Present:0, Absent:0, "Half Day":0, "On Leave":0, "Work From Home":0, total:0 };
				map[dept][r.status]++; map[dept].total++;
			});
			return map;
		}, [filteredAttendance]);

		const selectedEmpRecords = useMemo(() => {
			if (!selectedEmployee) return [];
			return filteredAttendance.filter(r=>r.employee===selectedEmployee).sort((a,b)=>a.date.localeCompare(b.date));
		}, [selectedEmployee, filteredAttendance]);

		const clearFilters = () => {
			setFilterDept(""); setFilterBranch(""); setFilterShift("");
			setFilterDesignation(""); setFilterStatus([]); setFilterEmployee(""); setSearchQuery("");
		};
		const hasActiveFilters = filterDept||filterBranch||filterShift||filterDesignation||filterStatus.length||filterEmployee||searchQuery;
		const toggleSort = (field) => {
			if (sortField===field) setSortDir(sortDir==="asc"?"desc":"asc");
			else { setSortField(field); setSortDir("asc"); }
		};
		return html`<div style=${{ fontFamily:"'DM Sans','Segoe UI',sans-serif", background:"#f8f7f4", minHeight:"100vh", color:"#1a1a2e" }}>
			<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,500;0,9..40,700;1,9..40,400&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet" />

			<!-- HEADER -->
			<div style=${{ background:"#fff", padding:"28px 36px", display:"flex", alignItems:"center", justifyContent:"space-between", borderBottom:"1px solid #e5e7eb", boxShadow:"0 1px 3px rgba(0,0,0,0.04)" }}>
				<div>
					<div style=${{ fontSize:22, fontWeight:700, color:"#1a1a2e", letterSpacing:-0.5 }}>Attendance HR Dashboard</div>
					<div style=${{ fontSize:12, color:"#6b7280", fontFamily:"'Space Mono',monospace", marginTop:2 }}>Attendance Management System</div>
				</div>
				<div style=${{ display:"flex", gap:10, alignItems:"center" }}>
					<span style=${{ fontSize:13, color:"#6b7280" }}>From</span>
					<${DatePicker} value=${fromDate} onChange=${v=>{ setFromDate(v); }} label="from" />
					<span style=${{ fontSize:13, color:"#6b7280" }}>To</span>
					<${DatePicker} value=${toDate} onChange=${v=>{ setToDate(v); }} label="to" />
				</div>
			</div>

			<div style=${{ maxWidth:1400, margin:"0 auto", padding:"24px 20px" }}>

				${loading && html`<div style=${{ background:"#fff", borderRadius:14, padding:"60px", textAlign:"center", color:"#6b7280", fontSize:14, border:"1px solid #e8e5df" }}>
					Loading attendance data…
				</div>`}

				${!loading && html`<div>

					<!-- FILTER BAR -->
					<div style=${{ background:"#fff", borderRadius:16, padding:"20px 24px", marginBottom:24, boxShadow:"0 1px 3px rgba(0,0,0,0.06)", border:"1px solid #e8e5df" }}>
						<div style=${{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:14 }}>
							<div style=${{ fontSize:13, fontWeight:700, textTransform:"uppercase", letterSpacing:1.5, color:"#6b7280", fontFamily:"'Space Mono',monospace" }}>Filters</div>
							${hasActiveFilters && html`<button onClick=${clearFilters} style=${{ background:"#fee2e2", color:"#991b1b", border:"none", borderRadius:8, padding:"6px 14px", fontSize:12, fontWeight:600, cursor:"pointer" }}>Clear All Filters</button>`}
						</div>
						<div style=${{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(180px,1fr))", gap:12 }}>
							<${CustomSelect} value=${filterEmployee}
								onChange=${v=>{ setFilterEmployee(v); if(v) setSelectedEmployee(v); }}
								placeholder="Search Employee..." searchable=${true}
								options=${[{value:"",label:"All Employees"},...employees.map(e=>({value:e.id,label:`${e.name} (${e.id})`}))]} />
							<${CustomSelect} value=${filterDept} onChange=${setFilterDept} placeholder="All Departments"
								options=${[{value:"",label:"All Departments"},...departments.map(d=>({value:d,label:d}))]} />
							<${CustomSelect} value=${filterBranch} onChange=${setFilterBranch} placeholder="All Branches"
								options=${[{value:"",label:"All Branches"},...branches.map(b=>({value:b,label:b}))]} />
							<${CustomSelect} value=${filterShift} onChange=${setFilterShift} placeholder="All Shifts"
								options=${[{value:"",label:"All Shifts"},...shifts.map(s=>({value:s,label:s}))]} />
							<${CustomSelect} value=${filterDesignation} onChange=${setFilterDesignation} placeholder="All Designations"
								options=${[{value:"",label:"All Designations"},...designations.map(d=>({value:d,label:d}))]} />
							<${MultiSelect} values=${filterStatus} onChange=${setFilterStatus} placeholder="All Status"
								options=${STATUSES.map(s=>({value:s,label:s}))} />
						</div>
					</div>

					<!-- TABS -->
					<div style=${{ display:"flex", gap:4, marginBottom:24 }}>
						${[["daily","Workday Tracker"],["employees","Employee List"],["department","Department View"],["overview","Overview"]].map(([key,label]) => html`
							<button key=${key} onClick=${()=>{ setActiveTab(key); setSelectedEmployee(null); }} style=${{
								padding:"10px 22px", borderRadius:10, border:"none", fontWeight:600, fontSize:13,
								cursor:"pointer", fontFamily:"'DM Sans',sans-serif", transition:"all 0.2s",
								background: activeTab===key?"#1a1a2e":"#fff",
								color:      activeTab===key?"#fff":"#6b7280",
								boxShadow:  activeTab===key?"0 4px 12px rgba(26,26,46,0.25)":"0 1px 2px rgba(0,0,0,0.05)",
							}}>${label}</button>
						`)}
					</div>

					<!-- ══ OVERVIEW ══ -->
					${activeTab==="overview" && html`<div>
						<div style=${{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))", gap:16, marginBottom:28 }}>
							${[
								{ label:"Total Records",   value:stats.total,             color:"#1a1a2e", accent:"#e94560" },
								{ label:"Present",          value:stats.Present,           color:"#166534", accent:"#22c55e" },
								{ label:"Absent",           value:stats.Absent,            color:"#991b1b", accent:"#ef4444" },
								{ label:"Half Day",         value:stats["Half Day"],       color:"#854d0e", accent:"#eab308" },
								{ label:"On Leave",         value:stats["On Leave"],       color:"#3730a3", accent:"#6366f1" },
								{ label:"Work From Home",   value:stats["Work From Home"], color:"#c2410c", accent:"#f97316" },
								{ label:"Late Entries",     value:stats.lateEntries,       color:"#9a3412", accent:"#f97316" },
							].map((card,i) => html`
								<div key=${i} style=${{ background:"#fff", borderRadius:14, padding:"20px 22px", border:"1px solid #e8e5df", boxShadow:"0 2px 8px rgba(0,0,0,0.04)", position:"relative", overflow:"hidden" }}>
									<div style=${{ position:"absolute", top:0, left:0, right:0, height:3, background:card.accent }} />
									<div style=${{ fontSize:12, color:"#9ca3af", fontWeight:600, textTransform:"uppercase", letterSpacing:1, fontFamily:"'Space Mono',monospace" }}>${card.label}</div>
									<div style=${{ fontSize:32, fontWeight:700, color:card.color, marginTop:6, fontFamily:"'Space Mono',monospace" }}>${card.value.toLocaleString()}</div>
									${stats.total>0 && card.label!=="Total Records" && card.label!=="Late Entries" && html`<div style=${{ fontSize:12, color:"#9ca3af", marginTop:4 }}>${((card.value/stats.total)*100).toFixed(1)}% of total</div>`}
								</div>
							`)}
						</div>

						<div style=${{ background:"#fff", borderRadius:14, padding:"22px 26px", marginBottom:24, border:"1px solid #e8e5df" }}>
							<div style=${{ fontSize:14, fontWeight:700, marginBottom:14 }}>Attendance Distribution</div>
							<div style=${{ display:"flex", height:36, borderRadius:10, overflow:"hidden", gap:2 }}>
								${STATUSES.map(s => {
									const pct = stats.total ? (stats[s]/stats.total)*100 : 0;
									if (!pct) return null;
									return html`<div key=${s} title=${`${s}: ${stats[s]} (${pct.toFixed(1)}%)`} style=${{ width:`${pct}%`, background:STATUS_COLORS[s].dot, display:"flex", alignItems:"center", justifyContent:"center", fontSize:11, fontWeight:700, color:"#fff", minWidth:0, transition:"width 0.5s ease" }}>
										${pct>6 ? `${pct.toFixed(0)}%` : ""}
									</div>`;
								})}
							</div>
							<div style=${{ display:"flex", gap:20, marginTop:12, flexWrap:"wrap" }}>
								${STATUSES.map(s => html`<div key=${s} style=${{ display:"flex", alignItems:"center", gap:6, fontSize:12, color:"#6b7280" }}>
									<div style=${{ width:10, height:10, borderRadius:3, background:STATUS_COLORS[s].dot }} />${s}
								</div>`)}
							</div>
						</div>

						<div style=${{ background:"#fff", borderRadius:14, padding:"22px 26px", border:"1px solid #e8e5df" }}>
							<div style=${{ fontSize:14, fontWeight:700, marginBottom:14 }}>Top Late Arrivals This Month</div>
							${employeeSummaries.length===0
								? html`<div style=${{ color:"#9ca3af", fontSize:13, textAlign:"center", padding:"20px 0" }}>No records for ${fromDate} – ${toDate}</div>`
								: [...employeeSummaries].sort((a,b)=>b.lateEntries-a.lateEntries).slice(0,5).map((emp,i) => html`
									<div key=${emp.id} style=${{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"10px 0", borderBottom:i<4?"1px solid #f3f4f6":"none" }}>
										<div style=${{ display:"flex", alignItems:"center", gap:12 }}>
											<div style=${{ width:34, height:34, borderRadius:10, background:avatarBg(emp.id), display:"flex", alignItems:"center", justifyContent:"center", fontWeight:700, fontSize:13, color:avatarFg(emp.id) }}>${initials(emp.name)}</div>
											<div>
												<div style=${{ fontWeight:600, fontSize:13 }}>${emp.name}</div>
												<div style=${{ fontSize:11, color:"#9ca3af" }}>${emp.department}${emp.designation?" · "+emp.designation:""}</div>
											</div>
										</div>
										<div style=${{ background:"#fff7ed", color:"#9a3412", padding:"4px 12px", borderRadius:8, fontSize:12, fontWeight:700, fontFamily:"'Space Mono',monospace" }}>${emp.lateEntries} late</div>
									</div>
								`)
							}
						</div>
					</div>`}

					<!-- ══ EMPLOYEES ══ -->
					${activeTab==="employees" && !selectedEmployee && html`
						<div style=${{ background:"#fff", borderRadius:14, border:"1px solid #e8e5df", overflow:"hidden" }}>
							<div style=${{ overflowX:"auto" }}>
								<table style=${{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
									<thead>
										<tr style=${{ background:"#f9fafb" }}>
											${[["name","Employee"],["department","Dept"],["designation","Role"],["Present","P"],["Absent","A"],["Half Day","HD"],["On Leave","L"],["Work From Home","WFH"],["totalWorkingDays","Work Days"],["total","Total"]].map(([field,label]) => html`
												<th key=${field} onClick=${()=>toggleSort(field)} style=${thStyle}>
													${label} ${sortField===field?(sortDir==="asc"?"↑":"↓"):""}
												</th>
											`)}
										</tr>
									</thead>
									<tbody>
										${employeeSummaries.map((emp,i) => html`
											<tr key=${emp.id} onClick=${()=>setSelectedEmployee(emp.id)}
												onMouseEnter=${e=>e.currentTarget.style.background="#f0f0ec"}
												onMouseLeave=${e=>e.currentTarget.style.background=i%2===0?"#fff":"#fafaf8"}
												style=${{ cursor:"pointer", background:i%2===0?"#fff":"#fafaf8", transition:"background 0.15s" }}>
												<td style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6" }}>
													<div style=${{ display:"flex", alignItems:"center", gap:10 }}>
														<div style=${{ width:32, height:32, borderRadius:8, background:avatarBg(emp.id), display:"flex", alignItems:"center", justifyContent:"center", fontWeight:700, fontSize:12, color:avatarFg(emp.id), flexShrink:0 }}>
															${initials(emp.name)}
														</div>
														<div>
															<div style=${{ fontWeight:600 }}>${emp.name}</div>
															<div style=${{ fontSize:11, color:"#9ca3af", fontFamily:"'Space Mono',monospace" }}>${emp.id}</div>
														</div>
													</div>
												</td>
												<td style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6", color:"#6b7280" }}>${emp.department||"—"}</td>
												<td style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6", color:"#6b7280" }}>${emp.designation||"—"}</td>
												${STATUSES.map(s => html`<td key=${s} style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6", textAlign:"center" }}>
													<span style=${{ background:STATUS_COLORS[s].bg, color:STATUS_COLORS[s].fg, padding:"3px 10px", borderRadius:6, fontWeight:700, fontSize:12, fontFamily:"'Space Mono',monospace" }}>${emp[s]}</span>
												</td>`)}
												<td style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6", textAlign:"center" }}><span style=${{ background:"#eff6ff", color:"#1d4ed8", padding:"3px 10px", borderRadius:6, fontWeight:700, fontSize:12, fontFamily:"'Space Mono',monospace" }}>${emp.totalWorkingDays}</span></td><td style=${{ padding:"12px 14px", borderBottom:"1px solid #f3f4f6", fontWeight:700, fontFamily:"'Space Mono',monospace", textAlign:"center" }}>${emp.total}</td>
											</tr>
										`)}
									</tbody>
								</table>
							</div>
							<div style=${{ padding:"14px 20px", background:"#f9fafb", fontSize:12, color:"#6b7280", borderTop:"1px solid #e5e7eb" }}>
								Showing ${employeeSummaries.length} employees · Click a row to view detailed attendance
							</div>
						</div>
					`}

					<!-- ══ EMPLOYEE DETAIL ══ -->
					${activeTab==="employees" && selectedEmployee && (() => {
						const emp = employeeSummaries.find(e=>e.id===selectedEmployee);
						if (!emp) return null;
						const lpEntries=(leaveRequests[emp.id]||[]).filter(l=>(l.status||"Open")==="Open"); const apEntries=(attRequests[emp.id]||[]).filter(a=>a.docstatus===0); return html`<${EmployeeDetail} emp=${emp} records=${selectedEmpRecords} lpCount=${lpEntries.reduce((s,l)=>{ const a=new Date(l.from+"T00:00:00"),b=new Date(l.to+"T00:00:00"); return s+Math.round((b-a)/86400000)+1; },0)} apCount=${apEntries.reduce((s,a)=>{ const x=new Date(a.from+"T00:00:00"),y=new Date(a.to+"T00:00:00"); return s+Math.round((y-x)/86400000)+1; },0)} lpEntries=${lpEntries} apEntries=${apEntries} onBack=${()=>{ setSelectedEmployee(null); setActiveTab("daily"); }} onRefresh=${()=>fetchData(true)} />`;
					})()}

					<!-- ══ DAILY VIEW ══ -->
					${activeTab==="daily" && html`
						<!-- Legend at top -->
						<div style=${{ background:"#fff", borderRadius:12, border:"1px solid #e8e5df", padding:"12px 20px", marginBottom:12, fontSize:11, color:"#6b7280", display:"flex", gap:16, flexWrap:"wrap", alignItems:"center" }}>
							<span style=${{fontWeight:700,color:"#1a1a2e",fontSize:12,fontFamily:"'Space Mono',monospace"}}>Legend:</span>
							${STATUSES.map(s => html`<span key=${s} style=${{display:"flex",alignItems:"center",gap:4}}>
								<span style=${{display:"inline-block",width:20,height:20,lineHeight:"20px",borderRadius:4,background:STATUS_COLORS[s].bg,color:STATUS_COLORS[s].fg,fontWeight:700,fontSize:9,textAlign:"center",fontFamily:"'Space Mono',monospace"}}>${ABBR[s]}</span>
								${s}
							</span>`)}
							<span style=${{display:"flex",alignItems:"center",gap:4}}><span style=${{display:"inline-block",width:20,height:20,lineHeight:"20px",borderRadius:4,background:"#ede9fe",color:"#7c3aed",fontWeight:700,fontSize:9,textAlign:"center",fontFamily:"'Space Mono',monospace"}}>Ho</span> Holiday</span>
							<span style=${{display:"flex",alignItems:"center",gap:4}}><span style=${{display:"inline-block",width:20,height:20,lineHeight:"20px",borderRadius:4,background:"#e5e7eb",color:"#6b7280",fontWeight:700,fontSize:9,textAlign:"center",fontFamily:"'Space Mono',monospace"}}>WO</span> Weekly Off</span>
							<span style=${{display:"flex",alignItems:"center",gap:4}}><span style=${{display:"inline-block",width:20,height:20,lineHeight:"20px",borderRadius:4,background:"#fed7aa",color:"#9a3412",fontWeight:700,fontSize:9,textAlign:"center",fontFamily:"'Space Mono',monospace"}}>LP</span> Leave Pending</span>
							<span style=${{display:"flex",alignItems:"center",gap:4}}><span style=${{display:"inline-block",width:20,height:20,lineHeight:"20px",borderRadius:4,background:"#a5f3fc",color:"#0e7490",fontWeight:700,fontSize:9,textAlign:"center",fontFamily:"'Space Mono',monospace"}}>AP</span> Attendance Request Pending</span>
						</div>
						<div style=${{ background:"#fff", borderRadius:14, border:"1px solid #e8e5df", overflow:"hidden" }}>
							<div style=${{ overflowX:"auto", maxHeight:600 }}>
								<table style=${{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
									<thead style=${{ position:"sticky", top:0, zIndex:2 }}>
										<tr style=${{ background:"#f9fafb" }}>
											<th style=${{ ...thStyle, position:"sticky", left:0, background:"#f9fafb", zIndex:3, cursor:"default", minWidth:160 }}>Employee</th>
											${rangeDates.map((ds,i)=>{
												const d = new Date(ds+"T00:00:00");
												const dow = ["Su","Mo","Tu","We","Th","Fr","Sa"][d.getDay()];
												const dd = d.getDate();
												const mm = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
												return html`<th key=${i} style=${{ ...thStyle, textAlign:"center", minWidth:38, background:"#f9fafb", fontSize:10, cursor:"default", padding:"8px 2px" }}>
													<div style=${{fontSize:8,color:"#9ca3af"}}>${mm}</div>
													<div>${dd}</div>
													<div style=${{color:"#9ca3af",fontSize:9}}>${dow}</div>
												</th>`;
											})}
										</tr>
									</thead>
									<tbody>
										${employeeSummaries.slice((dailyPage-1)*DAILY_PAGE_SIZE, dailyPage*DAILY_PAGE_SIZE).map((emp,ei) => {
											const empRecords = {};
											filteredAttendanceCells.filter(r=>r.employee===emp.id).forEach(r=>{ empRecords[r.date] = r.status; });
											return html`<tr key=${emp.id} style=${{ background:ei%2===0?"#fff":"#fafaf8" }}>
												<td onClick=${()=>{ setActiveTab("employees"); setSelectedEmployee(emp.id); }} style=${{ padding:"8px 12px", borderBottom:"1px solid #f3f4f6", whiteSpace:"nowrap", position:"sticky", left:0, background:ei%2===0?"#fff":"#fafaf8", zIndex:1, fontWeight:600, fontSize:12, cursor:"pointer" }}>
													<div style=${{color:"#1a1a2e",textDecoration:"underline",textDecorationStyle:"dotted"}}>${emp.name}</div>
													<div style=${{fontSize:10,color:"#9ca3af",fontFamily:"'Space Mono',monospace"}}>${emp.id}</div>
												</td>
												${rangeDates.map((dateStr,i)=>{
													const d = new Date(dateStr+"T00:00:00");
													const isSun = d.getDay()===0;
													const status = empRecords[dateStr];
													const empHol = employeeHolidays[emp.id] || {};
													const isHoliday = !status && (isSun || (empHol.dates && empHol.dates.has(dateStr)));
													const holInfo = (!status && empHol.dates && empHol.dates.has(dateStr)) ? (empHol.map[dateStr] || {}) : (!status && isSun) ? {weekly_off:true} : {};
													const lpEntry = !isHoliday ? (leaveRequests[emp.id]||[]).find(l=>dateStr>=l.from&&dateStr<=l.to && (l.status||"Open")==="Open") : null;
													const laEntry = !isHoliday && !lpEntry ? (leaveRequests[emp.id]||[]).find(l=>dateStr>=l.from&&dateStr<=l.to && (l.status||"")==="Approved") : null;
													const isLP = !!lpEntry;
													const isLA = !!laEntry;
													const apEntry = !isHoliday && !lpEntry && !laEntry ? (attRequests[emp.id]||[]).find(a=>dateStr>=a.from&&dateStr<=a.to) : null;
													const isAP = !!apEntry && apEntry.docstatus === 0;
													const isWO = isHoliday && holInfo.weekly_off;
													const cellBg = isWO?"#f3f4f6":isLP?"#ffedd5":isAP?"#cffafe":"transparent";
													return html`<td key=${i} style=${{ padding:"6px 2px", borderBottom:"1px solid #f3f4f6", textAlign:"center", background:cellBg, cursor:"pointer", opacity:filterStatus.length && !filterStatus.includes(status)?0.2:1, transition:"opacity 0.15s" }}>
														${isLP
															? html`<span title=${"Leave Pending" + (lpEntry.leave_type?" ("+lpEntry.leave_type+")":"")} style=${{ display:"inline-block", width:24, height:24, lineHeight:"24px", borderRadius:6, background:"#fed7aa", color:"#9a3412", fontWeight:700, fontSize:10, fontFamily:"'Space Mono',monospace" }}>LP</span>`
															: isLA
																? html`<span title=${"On Leave" + (laEntry.leave_type?" ("+laEntry.leave_type+")":"")} style=${{ display:"inline-block", width:24, height:24, lineHeight:"24px", borderRadius:6, background:STATUS_COLORS["On Leave"].bg, color:STATUS_COLORS["On Leave"].fg, fontWeight:700, fontSize:10, fontFamily:"'Space Mono',monospace" }}>L</span>`
																: isAP
																? html`<span title=${"Attendance Request Pending" + (apEntry.reason?" ("+apEntry.reason+")":"")} style=${{ display:"inline-block", width:24, height:24, lineHeight:"24px", borderRadius:6, background:"#a5f3fc", color:"#0e7490", fontWeight:700, fontSize:10, fontFamily:"'Space Mono',monospace" }}>AP</span>`
																: isHoliday
																	? html`<span title=${holInfo.description||(holInfo.weekly_off?"Weekly Off":"Holiday")} style=${{ display:"inline-block", width:24, height:24, lineHeight:"24px", borderRadius:6, background:holInfo.weekly_off?"#e5e7eb":"#ede9fe", color:holInfo.weekly_off?"#6b7280":"#7c3aed", fontWeight:700, fontSize:10, fontFamily:"'Space Mono',monospace", opacity:holInfo.weekly_off?0.6:0.8 }}>${holInfo.weekly_off?"WO":"Ho"}</span>`
																	: status
																		? html`<span style=${{ display:"inline-block", width:24, height:24, lineHeight:"24px", borderRadius:6, background:STATUS_COLORS[status].bg, color:STATUS_COLORS[status].fg, fontWeight:700, fontSize:10, fontFamily:"'Space Mono',monospace" }}>${ABBR[status]}</span>`
																		: html`<span style=${{color:"#e5e7eb"}}>.</span>`
													}
													</td>`;
												})}
											</tr>`;
										})}
									</tbody>
								</table>
							</div>
							${(()=>{
								const totalPages = Math.ceil(employeeSummaries.length / DAILY_PAGE_SIZE);
								const start = (dailyPage-1)*DAILY_PAGE_SIZE+1;
								const end = Math.min(dailyPage*DAILY_PAGE_SIZE, employeeSummaries.length);
								return html`<div style=${{ padding:"12px 20px", background:"#f9fafb", borderTop:"1px solid #e5e7eb", display:"flex", alignItems:"center", justifyContent:"space-between", flexWrap:"wrap", gap:8 }}>
									<div style=${{ fontSize:12, color:"#6b7280" }}>
										Showing <strong>${start}–${end}</strong> of <strong>${employeeSummaries.length}</strong> employees · Click name to view detail
									</div>
									${totalPages > 1 && html`<div style=${{ display:"flex", alignItems:"center", gap:4 }}>
										<button onClick=${()=>setDailyPage(1)} disabled=${dailyPage===1} style=${{ background:"none", border:"1px solid #e5e7eb", borderRadius:7, padding:"4px 10px", fontSize:12, cursor:dailyPage===1?"default":"pointer", color:dailyPage===1?"#d1d5db":"#374151", fontWeight:600 }}>«</button>
										<button onClick=${()=>setDailyPage(p=>Math.max(1,p-1))} disabled=${dailyPage===1} style=${{ background:"none", border:"1px solid #e5e7eb", borderRadius:7, padding:"4px 10px", fontSize:12, cursor:dailyPage===1?"default":"pointer", color:dailyPage===1?"#d1d5db":"#374151", fontWeight:600 }}>‹ Prev</button>
										${Array.from({length:totalPages},(_,i)=>i+1).filter(p=>Math.abs(p-dailyPage)<=2||p===1||p===totalPages).reduce((acc,p,idx,arr)=>{ if(idx>0&&arr[idx-1]!==p-1) acc.push("…"); acc.push(p); return acc; },[]).map((p,i)=>
											p==="…"
												? html`<span key=${i} style=${{padding:'4px 6px',color:'#9ca3af',fontSize:12}}>…</span>`
												: html`<button key=${p} onClick=${()=>setDailyPage(p)} style=${{ background:p===dailyPage?"#1a1a2e":"none", border:"1px solid "+(p===dailyPage?"#1a1a2e":"#e5e7eb"), borderRadius:7, padding:"4px 10px", fontSize:12, cursor:"pointer", fontWeight:600, color:p===dailyPage?"#fff":"#374151" }}>${p}</button>`
										)}
										<button onClick=${()=>setDailyPage(p=>Math.min(totalPages,p+1))} disabled=${dailyPage===totalPages} style=${{ background:"none", border:"1px solid #e5e7eb", borderRadius:7, padding:"4px 10px", fontSize:12, cursor:dailyPage===totalPages?"default":"pointer", color:dailyPage===totalPages?"#d1d5db":"#374151", fontWeight:600 }}>Next ›</button>
										<button onClick=${()=>setDailyPage(totalPages)} disabled=${dailyPage===totalPages} style=${{ background:"none", border:"1px solid #e5e7eb", borderRadius:7, padding:"4px 10px", fontSize:12, cursor:dailyPage===totalPages?"default":"pointer", color:dailyPage===totalPages?"#d1d5db":"#374151", fontWeight:600 }}>»</button>
									</div>`}
								</div>`;
							})()}
						</div>
					`}

					<!-- ══ DEPARTMENT ══ -->
					${activeTab==="department" && html`
						<div style=${{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(340px,1fr))", gap:18 }}>
							${Object.entries(deptBreakdown).map(([dept,data]) => html`
								<div key=${dept} style=${{ background:"#fff", borderRadius:14, border:"1px solid #e8e5df", overflow:"hidden" }}>
									<div style=${{ padding:"18px 22px", borderBottom:"1px solid #f3f4f6", display:"flex", justifyContent:"space-between", alignItems:"center" }}>
										<div style=${{ fontWeight:700, fontSize:15 }}>${dept}</div>
										<div style=${{ fontFamily:"'Space Mono',monospace", fontSize:12, color:"#6b7280" }}>${data.total} records</div>
									</div>
									<div style=${{ padding:"16px 22px" }}>
										${STATUSES.map(s => {
											const pct = data.total ? (data[s]/data.total)*100 : 0;
											return html`<div key=${s} style=${{ marginBottom:10 }}>
												<div style=${{ display:"flex", justifyContent:"space-between", fontSize:12, marginBottom:4 }}>
													<span style=${{ color:"#6b7280" }}>${s}</span>
													<span style=${{ fontWeight:700, fontFamily:"'Space Mono',monospace", color:STATUS_COLORS[s].fg }}>${data[s]} (${pct.toFixed(1)}%)</span>
												</div>
												<div style=${{ height:8, borderRadius:4, background:"#f3f4f6", overflow:"hidden" }}>
													<div style=${{ height:"100%", borderRadius:4, background:STATUS_COLORS[s].dot, width:`${pct}%`, transition:"width 0.5s ease" }} />
												</div>
											</div>`;
										})}
									</div>
								</div>
							`)}
						</div>
					`}

					<!-- FOOTER NOTE -->
					<div style=${{ marginTop:32, padding:"20px 24px", background:"#fff", borderRadius:14, border:"1px solid #e8e5df" }}>
						<div style=${{ fontSize:13, fontWeight:700, marginBottom:8, fontFamily:"'Space Mono',monospace", color:"#1a1a2e" }}>Implementation Notes for Frappe</div>
						<div style=${{ fontSize:12, color:"#6b7280", lineHeight:1.8 }}>
							This dashboard maps to Frappe HR's Attendance DocType. Key fields: employee, attendance_date, status (Present/Absent/Half Day/On Leave), shift, department, branch.
							Filters correspond to: Employee, Department, Branch, Shift Type, Designation (via Employee link), and Status.
							For check-in/out data, the Employee Checkin DocType is used via in_time/out_time fields.
						</div>
					</div>

				</div>`}

			</div>
		</div>`;
	}

	const root = ReactDOM.createRoot(mount);
	mount._reactRoot = root;
	root.render(React.createElement(AttDashboard));
}
