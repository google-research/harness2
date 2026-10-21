<?php

namespace Modules\Helpdesk\Services;

use Modules\Employee\Entities\Employee;

class HelpdeskService
{
    /**
     * Assign a ticket to an employee.
     *
     * VIOLATION: Directly coupling to Modules\Employee\Entities\Employee
     * violates Deptrac MOD-003 architecture boundary rule.
     * Refactor it to use EmployeeContractInterface in Modules/Core/Contracts.
     *
     * @param int $ticketId
     * @param int $employeeId
     * @return array
     */
    public function assignTicket(int $ticketId, int $employeeId): array
    {
        $employee = Employee::find($employeeId);

        if (!$employee) {
            throw new \InvalidArgumentException("Employee not found with ID {$employeeId}");
        }

        return [
            'ticket_id' => $ticketId,
            'assigned_to' => $employee->getFullName(),
            'department' => $employee->getDepartment(),
            'status' => 'assigned',
        ];
    }
}
